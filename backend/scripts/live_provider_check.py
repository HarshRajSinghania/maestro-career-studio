"""Live provider check (multi-provider plan, Task 14). Manual only: real keys, real network, a few
cents per run. Never imported by tests or CI.

It runs the calls the app makes, through the same code, against each provider you give a model
for, in a throwaway data folder (nothing touches the live install):

  text, json, image   `llm.call_openai`, the one-shot path every service uses
  tools_sequential    a two-step tool loop through `adapter.stream` + `replay.for_provider`
                      (tool B needs tool A's result), as `chat_agent.run_turn` runs it
  tools_parallel      two tool calls in one step (Gemini's call/response ordering)
  second_turn         a follow-up turn replaying the tool turn to the same model
                      (Claude thinking replay, Gemini thought signatures)
  switch              the tool turn's history answered by the next provider in the list

Keys come from the environment or from --env-file files (dotenv format, e.g. the live install's
maestro.env). Key values are never printed: anything that looks like a key is redacted.

From backend/:
  python scripts/live_provider_check.py --env-file ~/.config/maestro-live-check.env \\
      --openai gpt-5.6-luna --gemini gemini-3.8-flash \\
      --anthropic claude-sonnet-5-5,claude-haiku-5-5 --openrouter anthropic/claude-sonnet-5-5 \\
      [--openai-extra gpt-6-luna] [--openrouter-nozdr <model with no zero-retention endpoint>]
Exit status: 0 when every check passed.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

KEY_VARS = ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")
# Never inherited from the shell: they would redirect or decorate the requests under test.
SCRUB_VARS = (
    "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_CUSTOM_HEADERS",
    "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "SYNC_REMOTE_URL",
    "SYNC_KEY_FILE", "SYNC_PUBLIC_HOST", "DATABASE_URL", "FAST_MODEL", "SMART_MODEL", "CHAT_MODEL",
)
_KEYISH = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|AIza[A-Za-z0-9_\-]{10,})")
_SECRETS: list[str] = []


def redact(text: object) -> str:
    out = str(text)
    for secret in _SECRETS:
        out = out.replace(secret, "[key]")
    return _KEYISH.sub("[key]", out)


def load_keys(env_files: list[str]) -> None:
    from dotenv import dotenv_values

    for name in SCRUB_VARS:
        os.environ.pop(name, None)
    for path in env_files:
        values = dotenv_values(os.path.expanduser(path), interpolate=False)
        for name in KEY_VARS:
            if (values.get(name) or "").strip() and not os.environ.get(name):
                os.environ[name] = values[name].strip()
    _SECRETS.extend(os.environ[n] for n in KEY_VARS if os.environ.get(n))


def scratch_app(root: Path) -> None:
    """Point every app folder at `root` BEFORE app.config is imported, then migrate."""
    for name in ("DATA", "APPLICATIONS", "SETTINGS", "LOGS", "EXPORTS", "BASE_RESUMES",
                 "KB_DOCUMENTS"):
        (root / name.lower()).mkdir(parents=True, exist_ok=True)
        os.environ[f"{name}_DIR"] = str(root / name.lower())
    from app.services import seeding

    seeding.run_migrations()


def png_with_number(number: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (520, 160), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=72)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    draw.text((30, 40), f"CODE {number}", fill="black", font=font)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# --- the tool scenarios ------------------------------------------------------------------------

SYSTEM = "You are a test assistant. Use the tools when asked. Keep answers short."


def _specs():
    from app.services.llm_providers.types import ToolSpec

    obj = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
    return [
        ToolSpec("get_order_id", "Return the customer's order id.", obj({}, [])),
        ToolSpec("get_order_status", "Return where an order is. Needs the id from get_order_id.",
                 obj({"order_id": {"type": "string"}}, ["order_id"])),
        ToolSpec("get_weather", "Return today's weather for one city.",
                 obj({"city": {"type": "string"}}, ["city"])),
    ]


def _run_tool(name: str, args: dict | None) -> dict:
    args = args or {}
    if name == "get_order_id":
        return {"order_id": "A-7731"}
    if name == "get_order_status":
        if args.get("order_id") != "A-7731":
            return {"error": f"unknown order id {args.get('order_id')!r}"}
        return {"status": "delivered in Lisbon"}
    if name == "get_weather":
        city = str(args.get("city", ""))
        return {"city": city, "weather": {"paris": "rain", "tokyo": "sun"}.get(city.lower(), "fog")}
    return {"error": f"unknown tool {name}"}


class Turn:
    def __init__(self):
        self.rounds = 0
        self.calls: list[list[str]] = []  # tool names per model step
        self.reasoning = 0  # ReasoningPart count across steps
        self.signed_calls = 0  # tool calls carrying provider meta (Gemini thought signatures)
        self.reasoning_dropped = False
        self.usage = [0, 0]
        self.text = ""


def run_turn(provider_name: str, model: str, history: list, user_text: str,
             max_rounds: int = 6) -> Turn:
    """The chat agent's loop, minus persistence: append-only `history` in, replies appended."""
    from app.services.llm_providers import registry, replay
    from app.services.llm_providers.types import (
        Finish, Message, ModelRef, ReasoningPart, Request, TextPart, ToolResultPart,
    )

    ref = ModelRef(provider_name, model)
    provider = registry.get(provider_name)
    history.append(Message("user", [TextPart(user_text)]))
    turn = Turn()
    for _ in range(max_rounds):
        provider.ready()
        finish = None
        for event in provider.stream(Request(model=ref, system=SYSTEM, tools=_specs(), cache=True,
                                             messages=replay.for_provider(history, ref))):
            if isinstance(event, Finish):
                finish = event
        if finish is None:
            raise RuntimeError("stream ended without a Finish event")
        turn.rounds += 1
        turn.reasoning_dropped |= finish.reasoning_dropped
        if finish.usage:
            turn.usage[0] += finish.usage.input
            turn.usage[1] += finish.usage.output
        reply = finish.message
        turn.reasoning += sum(isinstance(p, ReasoningPart) for p in reply.parts)
        history.append(reply)
        calls = reply.tool_calls
        if not calls:
            turn.text = reply.text
            return turn
        turn.calls.append([c.name for c in calls])
        turn.signed_calls += sum(1 for c in calls if c.meta is not None)
        for call in calls:
            history.append(Message("tool", [ToolResultPart(
                call.id, json.dumps(_run_tool(call.name, call.arguments)))]))
    raise RuntimeError(f"no final answer after {max_rounds} model steps")


# --- checks -------------------------------------------------------------------------------------

class Results:
    def __init__(self):
        self.rows: list[tuple[str, str, str, bool, str, float]] = []

    def add(self, provider, model, check, ok, detail, seconds):
        self.rows.append((provider, model, check, ok, redact(detail)[:160], seconds))
        mark = "PASS" if ok else "FAIL"
        print(f"  {mark}  {check:<18} {seconds:5.1f}s  {redact(detail)[:160]}", flush=True)

    def run(self, provider, model, check, fn):
        start = time.monotonic()
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 — a failed check is a result, not a crash
            detail = f"{type(exc).__name__}: {exc}"
            extra = getattr(exc, "provider_detail", None)
            if extra and extra != str(exc):
                detail += f" | {extra}"
            ok = False
        self.add(provider, model, check, ok, detail, time.monotonic() - start)
        return ok


def one_shot_checks(res: Results, provider: str, model: str) -> None:
    from app.services import llm

    def text():
        out = llm.call_openai(prompt="Reply with exactly the word: pong", model=model,
                              response_format="text", max_retries=0, trace_name="live-check")
        return "pong" in str(out).lower(), repr(str(out)[:60])

    def as_json():
        out = llm.call_openai(prompt='Reply with only this JSON object: {"ok": true, "n": 7}',
                              model=model, response_format="json", max_retries=1,
                              trace_name="live-check")
        return isinstance(out, dict) and out.get("n") == 7, json.dumps(out)[:80]

    def image():
        out = llm.call_openai(prompt="What number follows the word CODE in this image? Reply with "
                                     "the digits only.", model=model, response_format="text",
                              images=[png_with_number("4821")], max_retries=0,
                              trace_name="live-check")
        return "4821" in str(out), repr(str(out)[:60])

    res.run(provider, model, "text", text)
    res.run(provider, model, "json", as_json)
    res.run(provider, model, "image", image)


SEQ_PROMPT = ("Where is my order? First call get_order_id, then call get_order_status with that "
              "id, then tell me the city in one short sentence.")


def tool_checks(res: Results, provider: str, model: str, store: dict) -> None:
    def sequential():
        history: list = []
        turn = run_turn(provider, model, history, SEQ_PROMPT)
        names = [n for step in turn.calls for n in step]
        ok = (names[:1] == ["get_order_id"] and "get_order_status" in names
              and "lisbon" in turn.text.lower())
        store["history"], store["turn"] = history, turn
        return ok, (f"steps={turn.calls} reasoning_parts={turn.reasoning} "
                    f"signed_calls={turn.signed_calls} usage={turn.usage} answer={turn.text[:60]!r}")

    def parallel():
        history: list = []
        turn = run_turn(provider, model, history,
                        "Get today's weather for Paris and for Tokyo. Request both in the same "
                        "step (two get_weather calls at once), then summarize in one sentence.")
        flat = [n for step in turn.calls for n in step]
        ok = flat.count("get_weather") >= 2 and "rain" in turn.text.lower() and "sun" in turn.text.lower()
        return ok, f"steps={turn.calls} answer={turn.text[:70]!r}"

    def second_turn():
        if "history" not in store:
            return False, "skipped: tools_sequential failed"
        history = list(store["history"])
        turn = run_turn(provider, model, history,
                        "Which city did you just name? Answer with one word.")
        return ("lisbon" in turn.text.lower() and not turn.reasoning_dropped,
                f"reasoning_dropped={turn.reasoning_dropped} answer={turn.text[:40]!r}")

    res.run(provider, model, "tools_sequential", sequential)
    res.run(provider, model, "tools_parallel", parallel)
    res.run(provider, model, "second_turn", second_turn)


def switch_check(res: Results, src: tuple[str, str, dict], dst: tuple[str, str]) -> None:
    src_provider, src_model, store = src
    dst_provider, dst_model = dst

    def switch():
        if "history" not in store:
            return False, f"skipped: no {src_provider} tool history"
        history = list(store["history"])
        turn = run_turn(dst_provider, dst_model, history,
                        "Which city was the order delivered in? Answer with one word.")
        return "lisbon" in turn.text.lower(), f"from {src_provider}: answer={turn.text[:40]!r}"

    res.run(dst_provider, dst_model, f"switch<-{src_provider}", switch)


def gpt6_check(res: Results, model: str) -> None:
    """Does a GPT-6 model need reasoning_effort "none" for tools, like GPT-5.4+ does?"""
    from app.services.llm_providers import openai_compat

    def plain():
        turn = run_turn("openai", model, [], SEQ_PROMPT)
        return "lisbon" in turn.text.lower(), f"without reasoning_effort: steps={turn.calls}"

    if res.run("openai", model, "tools(as shipped)", plain):
        return
    real = openai_compat.completion_extras
    openai_compat.completion_extras = lambda m: {"reasoning_effort": "none"}
    try:
        def with_none():
            turn = run_turn("openai", model, [], SEQ_PROMPT)
            return ("lisbon" in turn.text.lower(),
                    "WITH reasoning_effort none passes: extend _TOOLS_NEED_NO_REASONING")
        res.run("openai", model, "tools(effort=none)", with_none)
    finally:
        openai_compat.completion_extras = real


def nozdr_check(res: Results, model: str) -> None:
    from app.services import llm
    from app.services.llm_providers.errors import LLMProviderError

    def call():
        try:
            llm.call_openai(prompt="Reply with: ok", model=model, response_format="text",
                            max_retries=0, trace_name="live-check")
        except LLMProviderError as exc:
            said_zdr = "zero" in str(exc).lower() or "retention" in str(exc).lower()
            return said_zdr, f"sentence={str(exc)!r} | provider: {exc.provider_detail[:200]}"
        return False, "the call succeeded: this model has a zero-retention endpoint"

    res.run("openrouter", model, "no-ZDR sentence", call)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", action="append", default=[],
                        help="dotenv file to read keys from (repeatable); never printed")
    for name in ("openai", "anthropic", "gemini", "openrouter"):
        parser.add_argument(f"--{name}", default="", help="comma-separated model ids")
    parser.add_argument("--openai-extra", default="", help="GPT-6 model id for the effort check")
    parser.add_argument("--openrouter-nozdr", default="",
                        help="an OpenRouter model with no zero-retention endpoint")
    parser.add_argument("--skip-one-shot", action="store_true")
    args = parser.parse_args()

    load_keys(args.env_file)
    present = {n: bool(os.environ.get(n)) for n in KEY_VARS}
    print("keys:", ", ".join(f"{n.split('_')[0].lower()}={'yes' if v else 'no'}"
                             for n, v in present.items()))
    root = Path(tempfile.mkdtemp(prefix="maestro-live-check-"))
    scratch_app(root)
    print(f"scratch data: {root}\n")

    from app.services import model_settings  # noqa: F401 — after the scratch env is set

    key_of = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
              "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
    targets: list[tuple[str, str]] = []
    for provider in ("openai", "anthropic", "gemini", "openrouter"):
        for model in filter(None, (m.strip() for m in getattr(args, provider).split(","))):
            if not present[key_of[provider]]:
                print(f"skip {provider}/{model}: no {key_of[provider]}")
                continue
            targets.append((provider, model))

    if any(p == "openrouter" for p, _ in targets):
        # OpenRouter ids are routed by catalog tag (rule 1), as Find models would add them.
        from app.db import SessionLocal

        with SessionLocal() as session:
            for provider, model in targets:
                if provider == "openrouter":
                    model_settings.add_extra_model(session, model, "openrouter")

    res = Results()
    stores: dict[tuple[str, str], dict] = {}
    for provider, model in targets:
        print(f"== {provider} / {model}")
        if not args.skip_one_shot:
            one_shot_checks(res, provider, model)
        stores[(provider, model)] = {}
        tool_checks(res, provider, model, stores[(provider, model)])
        print()

    if len(targets) > 1:
        print("== provider switch (history from one provider, answered by the next)")
        for i, (provider, model) in enumerate(targets):
            dst = targets[(i + 1) % len(targets)]
            if dst[0] != provider:
                switch_check(res, (provider, model, stores[(provider, model)]), dst)
        print()

    if args.openai_extra and present["OPENAI_API_KEY"]:
        print(f"== GPT-6 tools rule: {args.openai_extra}")
        gpt6_check(res, args.openai_extra.strip())
        print()
    if args.openrouter_nozdr and present["OPENROUTER_API_KEY"]:
        from app.db import SessionLocal

        with SessionLocal() as session:
            model_settings.add_extra_model(session, args.openrouter_nozdr.strip(), "openrouter")
        print(f"== OpenRouter no-ZDR error: {args.openrouter_nozdr}")
        nozdr_check(res, args.openrouter_nozdr.strip())
        print()

    failed = [r for r in res.rows if not r[3]]
    print(f"{len(res.rows) - len(failed)}/{len(res.rows)} checks passed")
    for provider, model, check, _, detail, _ in failed:
        print(f"  FAIL {provider}/{model} {check}: {detail}")
    return 1 if failed or not res.rows else 0


if __name__ == "__main__":
    sys.exit(main())
