# Companion Queued Run Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> superpowers:subagent-driven-development in the planning session) to implement this plan task by task.

**Goal:** The Companion takes the user's Queued jobs, fills them a few tabs at a time, clicks Next
through every page, and stops on each review page so the user only presses Submit.

**Architecture:** A host-agnostic runner (`extension/shared/queue-run.js`) hosted by the side panel
drives one run record per tab through open → sign in → fill → advance → … → ready to submit. Page
work (classify, advance, sign in) is new content-script operations behind the existing service-worker
fan-out and frame gate. The backend gains a Queued-run list, a Companion sign-in hand-off, a
Companion "submitted" recorder, an `apply_url` on jobs, and a three-level "Screening answers" dial
that replaces the low-stakes switch.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (SQLite), pytest (+ xdist, Playwright Chromium, node
harness), Chrome MV3 extension (plain JS IIFEs on `window.careerStudioCompanion`), Next.js 16 / React 19.

**Design:** `docs/plans/2026-10-10-companion-queued-run-design.md` (approved 2026-10-10; Job step
buttons, park less and Part 8 added 2026-10-11). Read it first.

**Panel look:** direction C ("one next step"), per the design's "Panel look" decision. The panel
tasks (19, 20, 20a) render the run states into C's single Next card; ask the planner for the canvas
mockups before starting them.

**Task order:** 1 → 2–10 → 10a → 11 → 12–16 → 17 → 18 → 18a → 19 → 20 → 20a → 20b → (21) → 22 → 23. Where this plan and the design differ, this plan says so under "Adjustments" and why.

---

## Goal Card

Goal: Turn the Companion into the user's applying agent. It takes the jobs the user queued, fills
them several tabs at a time, moves through every page by itself and stops on the review page, so the
user only presses Submit. It asks the user only when it must, in the tab it is working in, and
otherwise gives the safer answer. Lowering the friction the user hit with the external bot ("it keeps
stopping for small things") is part of the goal.

Principles:
- **Submit is never clicked, at any setting.** When unsure whether a button is Next or Submit, stop.
  This beats every friction concern.
- **Stop only for the protected list or a wall** (CAPTCHA, email code, account creation). Anything
  else is answered, marked, and shown on the review page. Don't add confirmation steps.
- **Never overwrite the user's custom tailoring** (an open tailoring session or a tailored draft).
- **Passwords never reach a model, a trace, a receipt or telemetry.**

Non-goals:
- Runs that survive closing the panel or Chrome, schedules, or a cloud runner.
- Per-ATS fill procedures. Filling stays the generic AI-first loop; only Next/Submit buttons get a
  per-ATS table.
- Board-native apply (LinkedIn Easy Apply, Indeed Apply).

Autonomy: **peer** (Sonnet 5.5 implements, Opus 5.5 reviews, planned by Opus 5.5). You may adapt
*how* when the repo disagrees with this plan; log each deviation in the task's commit message body
as `Deviation: <planned> → <did>, because <reason>`. Anything that changes scope, a cross-task
interface named here, or the Goal Card goes back to the planner as a short deviation note. Never
widen scope.

---

## Adjustments to the design (planner's, made while planning)

1. **Parked questions are answered in the page itself, not in a new panel input.** Loop mode has
   no pause rows today (its report rows only jump to the field). A parked tab is left with the
   field focused; the panel lists the page's open questions (jump links) and a **Resume** button.
   Resume re-reads the page, ALWAYS learns the typed answers through the existing `learnAnswer`
   (`panel/actions/pause.js`; owner 2026-10-11: no checkbox), says "Saved N answers to your
   profile · Undo", and continues the run.
   This is the user's original "need you in the application itself", and avoids building a second
   answer widget for every field shape.
2. **The confirmation is recorded with consent channel `companion`,** not `frontend`, so the audit
   trail says the Companion observed it after the user's own Submit. `CONSENT_CHANNELS` gains it.
3. **Auto-mark's undo is a delay, not a reversal.** `submitted` is terminal in the proposal state
   machine, so the row shows "Marking applied in 8 s · Undo" and posts only after 8 s.
4. **Missing profile facts on the Queue view link to Settings › Profile** in the web app instead
   of an inline editor. They're still asked once and saved there.
5. **Dial migration:** the low-stakes switch stores no row when off, so "explicitly off" and "never
   touched" can't be told apart. Every install without a new dial row reads the default, **Backed by
   your history**. That turns on low-stakes answers for installs that had them off. Call it out in
   the CHANGELOG.
6. **Unclear buttons stop the run in v1** (treated as review). The model-picked `/api/autofill/page`
   is Task 21, behind the live check: build it only if the live check shows unclear buttons are a
   real source of stops.

---

## Conventions for every task

- Run Python from `backend/` with the backend venv. Backend suite: `pytest tests/ mcp_server/tests/
  -q -n auto --dist loadfile`. Extension tests alone: `pytest tests/test_extension_*.py tests/browser
  -q -rs -n auto --dist loadfile`. Browser tests need `python -m playwright install chromium`.
- Extension tests are Python in `backend/tests/`: node harness (`tests/extension_harness.py`
  `run_node`), panel harness (`tests/extension_panel_harness.py`), Playwright (`tests/browser/`).
  No JS test files exist in `extension/`.
- New content script → update `extension/manifest.json`,
  `tests/test_extension_manifest.py::test_the_content_script_is_the_split_modules_in_dependency_order`,
  `tests/extension_harness.py` (`PAGE_RUNTIME_SOURCES` or `FILL_ENGINE_SOURCES`, manifest order),
  and `tests/browser/conftest.py::ENGINE_SOURCES` if the engine needs it.
- New panel script → `extension/panel/panel.html` `<script>` tag (order rules in
  `tests/test_extension_panel.py::test_the_panel_document_is_self_contained_and_joins_the_namespace`;
  `fill-loop.js` must stay right after `guided-run.js`), plus the `actions.js` roster for an action
  file. A new `shared/` caller file → add to `tests/test_extension_page_messaging.py`'s file list.
- New page message type → `extension/sw.js` `BROADCASTABLE` (pinned exactly by
  `tests/test_extension_sw_router.py::test_the_broadcast_allow_list_is_pinned_and_not_the_harmless_ones`),
  and a handler in `content/agent.js` `PAGE_HANDLERS`.
- New `card` field → declare it in `panel.js`'s `card` literal, or `actionStore().write` throws.
- Public repo: test data uses made-up employers (Acme, Globex) and generic questions only.
- Commit after each task with a conventional message ending in
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (or the executing model's name).
- Don't touch SYSTEM.md until Task 22. It is at its line cap.

---

## Phase 0 — Measure first

### Task 1: Background-tab throttling spike

The default of 3 parallel tabs, and whether run tabs must take turns in the foreground, depends on
how much Chrome slows a fill in a background tab. This task writes a measurement script, runs it,
and records the numbers. It ships no product code.

**Files:**
- Create: `backend/scripts/spike_background_fill.py`
- Modify: `docs/plans/2026-10-10-companion-queued-run.md` (append a "Spike result" section at the end)

**Step 1: Write the script.** Headed Playwright Chromium (`headless=False`; headless does not
throttle like Chrome). Three pages in ONE browser context/window, loaded with
`tests/fixtures/browser/workday_text.html`, `workday_listbox.html` and `workday_date.html` and the
engine sources (`tests/browser/conftest.py::ENGINE_SOURCES`, injected with `add_script_tag` as the
`load` fixture does). For each page, time a sequence of `window.careerStudioCompanion.fillOps`
calls that mirror the existing browser tests: `inventory`, then `apply` with `op:"write"` on each
text field and `op:"pick"` on the listbox (copy the call shapes from `tests/browser/pages.py`
`inv`/`apply`). Measure three cases and print a table (median of 5 runs, ms):
1. the page in front (`page.bring_to_front()` before timing);
2. the page behind another page in the same window, timed immediately;
3. the same, after the page has sat in the background for 6 minutes (Chrome's intensive throttling
   starts after about 5). Use `time.sleep(370)` between bring-to-front of another page and timing.

```python
"""Spike: how much slower is a Companion fill in a background tab?

Run from backend/: python -m scripts.spike_background_fill
Needs a display (headed Chromium). Prints medians in ms; ships nothing.
"""
import statistics
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
EXT = ROOT / "extension"
FIX = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "browser"
SOURCES = ["shared/policy.js", "content/field-reader.js", "content/fill-base.js", "content/shapes.js",
           "content/recipes.js", "content/inventory.js", "content/fill-core.js", "content/fill-ops.js",
           "content/sections.js"]
FILL_JS = """async () => {
  const ops = window.careerStudioCompanion.fillOps;
  const t0 = performance.now();
  const inv = await ops.inventory({consentForms: false, runId: 'spike'});
  for (const f of inv.fields) {
    if (f.shape === 'text') await ops.apply([{fid: f.fid, op: 'write', value: 'Springfield'}]);
  }
  return performance.now() - t0;
}"""
# Adjust FILL_JS to the real inventory/apply signatures in tests/browser/pages.py before running.


def load(page, name):
    page.set_content((FIX / name).read_text(encoding="utf-8"))
    for src in SOURCES:
        page.add_script_tag(content=(EXT / src).read_text(encoding="utf-8"))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        pages = [ctx.new_page() for _ in range(3)]
        rows = []
        for case, settle in (("front", None), ("background", 0), ("background 6 min", 370)):
            times = []
            for _ in range(5):
                for i, name in enumerate(["workday_text.html", "workday_listbox.html", "workday_date.html"]):
                    load(pages[i], name)
                target = pages[0]
                if case == "front":
                    target.bring_to_front()
                else:
                    pages[1].bring_to_front()
                    if settle:
                        time.sleep(settle)
                times.append(target.evaluate(FILL_JS))
            rows.append((case, statistics.median(times)))
        for case, ms in rows:
            print(f"{case:20s} {ms:8.0f} ms")
        browser.close()


if __name__ == "__main__":
    main()
```

**Step 2: Run it** from `backend/`: `python -m scripts.spike_background_fill` (about 35 minutes
because of the 6-minute case; run it with `run_in_background`). Expected: three rows of medians.

**Step 3: Record the decision** in a "Spike result" section at the end of this plan, using this
rule:
- background ≤ 1.5× front, even after 6 min → **default 3 parallel tabs, all in the background**;
- otherwise → **take turns**: the runner keeps the run window's ACTIVE tab on the tab currently
  filling (`chrome.tabs.update(tabId, {active: true})` inside the run window only; the user's own
  window is never touched). Parallel then means "others wait or idle while one fills", and the
  default becomes 1 filling + up to 10 waiting at Ready to submit.
Task 19 reads this section.

**Step 4: Commit** `chore(spike): measure Companion fill time in background tabs`.

---

## Phase 1 — Backend

### Task 2: The "Screening answers" level (storage + settings API)

**Files:**
- Modify: `backend/app/services/model_settings.py` (after `AUTOFILL_LOW_STAKES_KEY`, line ~252)
- Modify: `backend/app/routers/settings.py:77-81` (`AutofillOptions`) and `:619-627` (routes)
- Test: `backend/tests/test_settings_autofill_options.py` (rewrite its three tests)

**Step 1: Write the failing tests** (replace the file's tests):

```python
from app.services import model_settings


def test_the_answer_level_defaults_to_backed_by_history(db_session):
    assert model_settings.get_answer_level(db_session) == "history"
    assert model_settings.get_autofill_low_stakes(db_session) is True


def test_each_level_round_trips_and_facts_only_turns_low_stakes_off(db_session):
    for level in ("facts", "history", "queued"):
        model_settings.set_answer_level(db_session, level)
        assert model_settings.get_answer_level(db_session) == level
    model_settings.set_answer_level(db_session, "facts")
    assert model_settings.get_autofill_low_stakes(db_session) is False


def test_the_options_endpoint_reads_and_writes_the_level(client):
    assert client.get("/api/settings/autofill-options").json() == {"answer_level": "history"}
    response = client.put("/api/settings/autofill-options", json={"answer_level": "queued"})
    assert response.status_code == 200 and response.json() == {"answer_level": "queued"}


def test_the_options_endpoint_refuses_an_unknown_level_or_the_old_key(client):
    assert client.put("/api/settings/autofill-options", json={"answer_level": "yolo"}).status_code == 422
    assert client.put("/api/settings/autofill-options", json={"low_stakes": True}).status_code == 422
```

**Step 2: Run** `pytest tests/test_settings_autofill_options.py -v`. Expected: FAIL
(`get_answer_level` is not defined).

**Step 3: Implement.** In `model_settings.py`:

```python
ANSWER_LEVEL_KEY = "autofill.answer_level"
AnswerLevel = Literal["facts", "history", "queued"]
ANSWER_LEVELS: tuple[str, ...] = ("facts", "history", "queued")
DEFAULT_ANSWER_LEVEL: AnswerLevel = "history"


def get_answer_level(session: Session | None = None) -> AnswerLevel:
    """How far a fill answers without asking (docs/plans/2026-10-10-companion-queued-run-design.md
    Part 3). Read by the server on every /map, /pick and /step; never taken from the client.
    facts: saved answers and career history only. history (default): plus the low-stakes kinds
    and history-backed "N years of a skill" Yes. queued: plus every requirement question on a
    job the user queued. The protected list sits outside every level."""
    value = _read(session, ANSWER_LEVEL_KEY)
    return value if value in ANSWER_LEVELS else DEFAULT_ANSWER_LEVEL


def set_answer_level(session: Session, level: AnswerLevel) -> None:
    if level not in ANSWER_LEVELS:
        raise ValueError(f"unknown answer level {level!r}")
    _set_raw_value(session, ANSWER_LEVEL_KEY, level)
    # The old switch is derived from the level now; drop its row so nothing reads a stale one.
    _set_raw_value(session, AUTOFILL_LOW_STAKES_KEY, None)
```

Then make the old reader derived, so `/map`, `autofill_pick.pick` and `autofill_step.step` keep
working unchanged:

```python
def get_autofill_low_stakes(session: Session | None = None) -> bool:
    """Low-stakes answers are on at every level but "facts" (derived from the answer level)."""
    return get_answer_level(session) != "facts"
```

Delete `set_autofill_low_stakes`, then grep for its callers and move them to `set_answer_level`
(`set_answer_level(db, "history" if on else "facts")`). In `routers/settings.py`:

```python
class AutofillOptions(BaseModel):
    """GET/PUT /api/settings/autofill-options — how far a fill answers without asking."""
    model_config = ConfigDict(extra="forbid")
    answer_level: Literal["facts", "history", "queued"]
```

GET returns `AutofillOptions(answer_level=model_settings.get_answer_level(db))`; PUT calls
`set_answer_level(db, payload.answer_level)` and returns the GET.

**Step 4: Run** the new tests, then `pytest tests/test_autofill_map.py tests/test_autofill_pick.py
tests/test_autofill_step.py tests/test_autofill_fill_router.py -q`. Fix any test that seeded the
switch through `set_autofill_low_stakes(db, True)` by seeding `set_answer_level(db, "history")`, and
`False` by `"facts"`. Tests that relied on "off by default" now need an explicit `"facts"` seed.
Expected: PASS.

**Step 5: Commit** `feat(settings): a three-level Screening answers setting replaces the low-stakes switch`.

### Task 3: The answer level reaches the map, the reasoned pass and pick

Level 2 adds "N years of a skill: Yes when the history shows the skill and does not contradict the
years". Level 3 adds "every requirement or experience question on a Queued job: Yes". Neither ever
covers the protected list: work authorization, sponsorship, age/eligibility, EEO, background or
criminal history, clearance, salary, legal attestation or signature.

**Files:**
- Modify: `backend/app/services/autofill_map.py` (`map_fields`, `low_stakes_scope`,
  `_NEVER_LOW_STAKES_TEMPLATE`, `_LOW_STAKES_TEMPLATE`)
- Modify: `backend/app/services/autofill_reasoned.py` (`reason`, its prompt)
- Modify: `backend/app/routers/autofill.py` (`post_map` ~403, `_facts` ~345: compute `queued`)
- Modify: `backend/app/services/autofill_pick.py:352` (pass the level through where `keen` is used)
- Test: `backend/tests/test_autofill_map.py`, `backend/tests/test_autofill_reasoned.py` (create it if
  absent), `backend/tests/test_autofill_fill_router.py`

**Step 1: Write failing tests.** Use the existing `run(fields, db_session, *, eeo_consented=True,
low_stakes=False)` helper in `test_autofill_map.py:69`. Extend it with
`level: str = "facts"` and `queued: bool = False`, mapping `low_stakes = level != "facts"`. Mirror
how neighbouring tests stub the engines (look at
`test_low_stakes_is_asked_only_when_on_and_only_for_fields_no_fact_answers` at line 146 for the
fake-engine pattern).

```python
def test_years_with_a_skill_stays_on_the_never_list_below_the_queued_level(db_session):
    scope, never = autofill_map.low_stakes_scope({}, level="history", queued=True)
    assert "number of years of experience" in never


def test_the_queued_level_moves_requirement_questions_into_scope_only_for_a_queued_job(db_session):
    scope, never = autofill_map.low_stakes_scope({}, level="queued", queued=True)
    assert "number of years of experience" not in never
    assert "requirement" in scope and "years" in scope
    assert "work authorization" in never and "sponsorship" in never and "salary" in never
    _, never_not_queued = autofill_map.low_stakes_scope({}, level="queued", queued=False)
    assert "number of years of experience" in never_not_queued


def test_the_protected_list_survives_every_level():
    for level in ("facts", "history", "queued"):
        _, never = autofill_map.low_stakes_scope({}, level=level, queued=True)
        for protected in ("work authorization", "sponsorship", "EEO", "criminal history",
                          "security clearance", "salary", "legal attestation"):
            assert protected in never, (level, protected)
```

In `test_autofill_reasoned.py`, pin the level-2 rule in the reasoning prompt the way existing
reasoned tests pin wording (find them with `grep -rn "autofill_reasoned" backend/tests`):

```python
def test_history_level_lets_a_years_question_be_yes_when_the_history_shows_the_skill(monkeypatch, db_session):
    prompt = autofill_reasoned.prompt_rules(level="history")
    assert "shows the skill" in prompt and "does not contradict" in prompt


def test_facts_level_keeps_years_questions_to_computed_years_only(db_session):
    prompt = autofill_reasoned.prompt_rules(level="facts")
    assert "shows the skill" not in prompt
```

In `test_autofill_fill_router.py`, a request on an application whose job has an `accepted` proposal
reaches `map_fields` with `queued=True` (monkeypatch `autofill_map.map_fields` to capture kwargs).
Without a proposal, `queued=False`.

**Step 2: Run** them. Expected: FAIL.

**Step 3: Implement.**
- `low_stakes_scope(facts, *, level="history", queued=False)`. When `level == "queued" and queued`:
  remove the clause `a number of years of experience with a specific skill or tool, such as …` from
  the never-list, and append to the scope: `"; any requirement, qualification or experience
  self-assessment for this job, including years with a named skill or tool (answered Yes)"`. Keep
  the module-level `_LOW_STAKES, _NEVER_LOW_STAKES = low_stakes_scope({})` defaults (level "history").
- `map_fields(..., low_stakes: bool, level: str = "history", queued: bool = False)` passes both
  through to `_low_stakes` → `low_stakes_scope`.
- `autofill_reasoned`: add `prompt_rules(level: str) -> str` returning today's rules, plus, when
  `level in ("history", "queued")`, the sentence: `"A yes/no question asking for at least N years
  with a skill or tool: answer Yes when the work history shows that skill and nothing in it
  contradicts N years (for example the total years of work cover N); answer none when the history
  clearly shows fewer years."` `reason(...)` takes `level` and builds its prompt with `prompt_rules`.
- `routers/autofill.py`: a helper `_queued(db, application_id) -> bool` is True when the
  application's job has an `ApplicationProposal` with status `accepted` or `approved`.
  `post_map` passes `level=model_settings.get_answer_level(db), queued=_queued(db, payload.application_id)`.
  Pass `level` into the reasoned pass the same way at its call site (`autofill_pick.py:388`).
- `autofill_pick.py:352`: the low-stakes paragraph uses the same `low_stakes_scope(facts, level=,
  queued=)` so /pick and /map state the same scope.

**Step 4: Run** `pytest tests/test_autofill_map.py tests/test_autofill_pick.py
tests/test_autofill_reasoned.py tests/test_autofill_fill_router.py tests/test_autofill_step.py -q`.
Expected: PASS.

**Step 5: Commit** `feat(autofill): the answer level widens low-stakes and reasoned answers; the protected list never moves`.

### Task 4: The Job Search Brief carries the answer level

**Files:**
- Modify: `backend/app/services/job_search_brief.py` (`build_brief`, ~line 185)
- Modify: `backend/app/schemas/job_search_brief.py` (`JobSearchBriefResponse`, ~line 72)
- Modify: `backend/mcp_server/server.py:437` (`get_job_search_brief` docstring: mention `applying.answer_level`)
- Test: `backend/tests/test_job_search_brief.py`

**Step 1: Failing test:**

```python
def test_the_brief_carries_the_answer_level(client, db_session):
    model_settings.set_answer_level(db_session, "queued")
    assert client.get("/api/jobs/search-brief").json()["applying"] == {"answer_level": "queued"}
```

**Step 2: Run.** Expected: FAIL (KeyError).

**Step 3: Implement** `_applying_block(db) -> {"answer_level": model_settings.get_answer_level(db)}`.
Add `"applying": _applying_block(db)` next to `"auto_apply"`, and a schema
`BriefApplying(answer_level: Literal["facts","history","queued"])` with `applying: BriefApplying` on the
response. Keep the MCP docstring under the truncation ratchet
(`test_registered_tool_docstrings_fit_client_truncation_budget`): one short clause.

**Step 4: Run** `pytest tests/test_job_search_brief.py mcp_server/tests -q`. Expected: PASS.

**Step 5: Commit** `feat(brief): agents read the Screening answers level`.

### Task 5: The Settings dial replaces the switch

**Files:**
- Modify: `frontend/components/settings/autofill-section.tsx:1264` and `:1276-1345` (`LowStakesSwitch`)
- Modify: `frontend/lib/types.ts:318-322` (`AutofillOptions`)
- Test: `backend/tests/test_frontend_settings_cards.py`, `backend/tests/test_frontend_settings_assistant_words.py`

**Step 1: Update the wording tests first.**
`test_frontend_settings_assistant_words.py::test_the_consent_words_keep_every_promise` and
`::test_the_consent_switches_keep_their_promises` pin "It never moves to the next page and never
submits." / "It never moves to the next page or submits." Change both pins to the new promise,
which is exact text in the component:
`"It never submits. Only during a run of your queued jobs does it move to the next page."`
Add a pin in `test_frontend_settings_cards.py` that the card renders the three level labels
`Facts only`, `Backed by your history`, `Queued means I qualify`, and that `low_stakes` no longer
appears in `autofill-section.tsx`.

**Step 2: Run** `pytest tests/test_frontend_settings_cards.py tests/test_frontend_settings_assistant_words.py -q`.
Expected: FAIL.

**Step 3: Implement.** `AutofillOptions` becomes `{ answer_level: "facts" | "history" | "queued" }`.
Rename `LowStakesSwitch` to `ScreeningAnswers`: the same query/mutation, a radio group (the repo's
Base UI `RadioGroup`; copy the pattern from another settings radio group, `grep -rn RadioGroup
frontend/components/settings`). Label "Screening answers", one short line under each option:
- **Facts only**: "Only your saved answers and career history. Anything else waits for you."
- **Backed by your history** (default): "Also answers low-stakes questions in the job's favour, and
  years-with-a-skill questions when your history shows the skill."
- **Queued means I qualify**: "On jobs you queued, also says yes to every requirement question."
Under the group, one line: "Work authorization, sponsorship, salary, EEO, background, clearance and
legal statements always wait for a saved answer." Toast: `Screening answers: <label>`. Keep the
copy rules (`docs/frontend-conventions.md`; ratchets `test_frontend_vocabulary.py`,
`test_frontend_sentence_length.py`). Replace the old promise sentence with the new one from Step 1.

**Step 4: Run** the two test files plus `test_frontend_vocabulary.py test_frontend_sentence_length.py`,
then `cd frontend && npx tsc --noEmit && npm run lint`. Expected: PASS.

**Step 5: Commit** `feat(settings): Screening answers dial`.

### Task 6: Jobs remember the employer's apply link

**Files:**
- Create: `backend/migrations/versions/<12-hex>_jobs_apply_url.py` (generate the id with
  `python -c "import uuid; print(uuid.uuid4().hex[:12])"`; `down_revision = "559244b575bc"`, which is
  the current head; confirm with `alembic heads` first)
- Modify: `backend/app/models/job.py` (beside `source_url`, line 24)
- Modify: `backend/app/schemas/job.py` (`JobCreate`, `JobIngest`, `JobSummary`)
- Modify: `backend/app/routers/jobs.py` (`create_job` ~300, `_persist_job` ~252)
- Modify: `backend/app/services/sync/*` only if the sync row serializers list job columns
  explicitly (`grep -rn "source_url" backend/app/services/sync`); add `apply_url` beside it
- Test: `backend/tests/test_jobs_apply_url.py` (new)

**Step 1: Failing tests:**

```python
def test_saving_a_job_keeps_the_employer_apply_link(client, monkeypatch):
    _stub_extraction(monkeypatch)  # copy the jd_extraction stub used by tests/test_jobs_router.py
    body = {"raw_text": "Title: Data Engineer\nCompany: Acme", "source_url": "https://board.example/jobs/1",
            "apply_url": "https://acme.example/careers/apply/1"}
    job = client.post("/api/jobs", json=body).json()
    assert job["apply_url"] == "https://acme.example/careers/apply/1"


def test_an_apply_link_must_be_http(client, monkeypatch):
    _stub_extraction(monkeypatch)
    body = {"raw_text": "x", "apply_url": "javascript:alert(1)"}
    assert client.post("/api/jobs", json=body).status_code == 422


def test_a_saved_again_job_gains_an_apply_link_it_lacked(client, monkeypatch):
    ...  # second POST with the same text and an apply_url fills a NULL apply_url; never overwrites one
```

**Step 2: Run.** Expected: FAIL.

**Step 3: Implement.** Column `apply_url: Mapped[str | None] = mapped_column(Text)`. The migration
uses `op.batch_alter_table("jobs")` → `add_column(sa.Column("apply_url", sa.Text(), nullable=True))`
(style: `559244b575bc_base_resume_anchors.py`). Schemas: `apply_url: HttpUrlStr | None = None` with a
validator that accepts only `http(s)://` and caps at 2048 characters. Add it to `JobCreate`,
`JobIngest` and `JobSummary`. `_persist_job` writes it on create; on dedup it fills only a NULL.

**Step 4: Run** the new tests, `pytest tests/test_jobs_router.py tests/sync -q`, and
`alembic upgrade head` against a scratch SQLite file. Expected: PASS.

**Step 5: Commit** `feat(jobs): remember the employer's apply link`.

### Task 7: Companion sign-in hand-off

**Files:**
- Create: `backend/app/companion_origin.py`
- Modify: `backend/app/services/proposals.py:71-87` (`share_job_site_login` gains a `channel` argument)
- Create: `backend/app/routers/companion.py` (prefix `/api/companion`); mount it in `backend/app/main.py`
- Test: `backend/tests/test_companion_job_site_login.py` (new)

**Step 1: Failing tests.** Seed full automation, a job-site login file (`monkeypatch.setattr(settings,
"settings_dir", tmp_path)` then `job_site_login.write(...)`, as `tests/test_job_site_login_handoff.py`
does), and an `accepted` proposal.

```python
EXT = {"Origin": "chrome-extension://pjmfonfapjdabkoicnelpflpjojdjgan"}


def test_the_companion_gets_the_login_for_a_queued_job_and_it_is_audited(client, queued):
    r = client.post("/api/companion/job-site-login", json={"proposal_id": str(queued.id)}, headers=EXT)
    assert r.status_code == 200 and set(r.json()) == {"email", "password"}
    event = latest_consent_event(queued.id)
    assert (event.action, event.channel) == ("login_shared", "companion")
    assert "password" not in (event.note or "")


def test_no_extension_origin_means_no_login(client, queued):
    for headers in ({}, {"Origin": "http://localhost:3000"}, {"Origin": "chrome-extension://someoneelse"}):
        r = client.post("/api/companion/job-site-login", json={"proposal_id": str(queued.id)}, headers=headers)
        assert r.status_code == 403


def test_the_same_gates_as_the_agent_hand_off(client, queued, db_session):
    ...  # full automation off → 409; company blocked → 409; proposal pending_review → 409; no login saved → 404
```

The test client's Host must pass `TrustedHostMiddleware` (`conftest` adds `testserver`), and the
origin guard refuses unlisted origins before routing, which is what the `someoneelse` case checks.

**Step 2: Run.** Expected: FAIL (404 route).

**Step 3: Implement.**

```python
# backend/app/companion_origin.py
"""Is this request from the configured Companion? An exact chrome-extension:// origin, never a
pattern (SYSTEM.md inv-browser-boundary item 3)."""
from fastapi import Request

from app.config import settings


def is_companion(request: Request) -> bool:
    origin = request.headers.get("origin", "")
    return origin in {f"chrome-extension://{ext_id}" for ext_id in settings.maestro_cs_extension_ids}
```

`share_job_site_login(session, prop, agent, *, channel="mcp")` writes `ConsentEvent(...,
channel=channel, note=agent)`. The new router:

```python
router = APIRouter(prefix="/api/companion", tags=["companion"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: UUID


@router.post("/job-site-login")
def companion_job_site_login(payload: LoginRequest, request: Request, db: ...):
    """The saved job-site login for a Queued job, for the Companion's sign-in step only.
    Never reaches a model: the Companion types it into the job's own sign-in page."""
    if not is_companion(request):
        raise HTTPException(403, detail="Only the Companion can ask for the job-site login here.")
    prop = db.get(ApplicationProposal, payload.proposal_id)
    if prop is None:
        raise HTTPException(404, detail="Proposal not found")
    try:
        return proposals.share_job_site_login(db, prop, "companion", channel="companion")
    except proposals.TransitionError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
```

Clean the 422 the same way `routers/settings.py:240-253` does. The body never carries the password,
but keep it uniform.

**Step 4: Run** the new test plus `tests/test_job_site_login_handoff.py tests/test_security_boundaries.py -q`.
Expected: PASS.

**Step 5: Commit** `feat(companion): the saved job-site login for the Companion's sign-in step`.

### Task 8: `GET /api/autofill/queue-run`

One read that returns what a run needs, with every server-side gate already applied.

**Files:**
- Create: `backend/app/services/queue_run.py`
- Modify: `backend/app/routers/autofill.py` (new route)
- Modify: `backend/app/schemas/autofill_fill.py` (response models)
- Test: `backend/tests/test_queue_run.py` (new)

**Response shape:**

```python
class QueueRunItem(BaseModel):
    proposal_id: UUID
    job_id: UUID
    company: str | None
    title: str | None
    open_url: str | None              # apply_url, else source_url; None → the item is skipped
    application_id: UUID | None
    base_resume: str | None           # the application's base, else the best-scored base for the job
    pdf_ready: bool                   # Application.pdf_path is set
    has_draft: bool                   # application.customized_json is not None
    tailoring_open: bool              # an open TailoringSession for (job, base_resume)
    knockout: Literal["conflict", "incomplete_profile", "clear", "unstated"]
    knockout_kinds: list[str]         # kinds of the conflicting / incomplete checks
    board_native: bool                # open_url host is linkedin.com / indeed.com AND apply_url is None


class QueueRunResponse(BaseModel):
    items: list[QueueRunItem]
    cap_left: int
    skipped: list[dict]               # [{proposal_id, why}] why ∈ blocked | applied | no_link | cap
```

**Step 1: Failing tests** (seed with `tests/test_proposals_models.py::_mk_job` plus proposals):
- 409 with `detail` naming full automation when the switch is off;
- only `accepted` proposals appear, oldest first;
- a blocked company, an already-applied application (`Application.status == "applied"`) and a job
  with no url each land in `skipped` with their `why`;
- with `max_submissions_per_day=2` and one reservation in the last 24 h (`cap_status`), only 1 item is
  returned and the rest are `skipped` with `why == "cap"`;
- `open_url` prefers `apply_url`;
- `base_resume` comes from the application, else the highest-composite base `AtsScore` for the job
  (find the base-score query used by `services/ats_score.py`; reuse it rather than writing SQL), else None;
- `tailoring_open`, `has_draft` and `pdf_ready` reflect the seeded rows;
- `knockout` uses `knockout.scan_for(db, job)` (read once per job; `scan_args` once per request).

**Step 2: Run.** Expected: FAIL.

**Step 3: Implement** `queue_run.build(session) -> QueueRunResponse`. Reuse:
`auto_apply_settings.get_settings`, `proposals._company_is_blocked` (rename it to public
`company_is_blocked` and update its callers), `proposals.cap_status`, `proposals.expire_stale`
(call it first, as `list_proposals` does), and `knockout.scan_args` / `scan_job`. The route is
`@router.get("/queue-run", response_model=QueueRunResponse)`, and a `TransitionError`-like guard →
409 when full automation is off.

**Step 4: Run** `pytest tests/test_queue_run.py -q`. Expected: PASS.

**Step 5: Commit** `feat(autofill): the Queued-run list with every gate applied`.

### Task 9: `POST /api/proposals/{id}/companion-submitted`

**Files:**
- Modify: `backend/app/services/proposals.py` (`CONSENT_CHANNELS` gains `"companion"`; new
  `record_companion_submit`)
- Modify: `backend/app/routers/proposals.py`
- Test: `backend/tests/test_companion_submitted.py` (new)

**Step 1: Failing tests:**

```python
def test_a_confirmation_after_the_users_submit_records_the_job_applied(client, queued):
    body = {"confirmation": {"host": "acme.example", "path": "/apply/done", "text": "Thank you for applying"}}
    r = client.post(f"/api/proposals/{queued.id}/companion-submitted", json=body, headers=EXT)
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    # application flips to applied; two ConsentEvents (approved, submitted) with channel companion;
    # evidence holds a final_review row and a submission_receipt row; cap reserved once


def test_only_the_companion_may_record_it(client, queued):
    assert client.post(..., headers={}).status_code == 403


def test_it_respects_the_cap_and_the_skip_list(client, queued, db_session):
    ...  # cap used → 409; company blocked after queueing → 409 (the approve step re-checks it)


def test_the_confirmation_text_is_bounded(client, queued):
    ...  # text > 300 chars → 422; host/path required
```

**Step 2: Run.** Expected: FAIL.

**Step 3: Implement** `record_companion_submit(session, prop, confirmation: dict) ->
ApplicationProposal`. In one transaction:
1. add a `final_review` evidence row from `get_final_review(session, prop)`, using the shape the
   `POST /{id}/evidence` route stores, so the approved gate passes;
2. `transition(session, prop, "approved", consent={"channel": "companion", "note": "Queued run: user submitted"})`;
3. add a `submission_receipt` evidence row `{"kind": "submission_receipt", "host", "path", "text"}`;
4. `transition(session, prop, "submitted", consent={"channel": "companion", "note": ...})`.

Read `transition` (line 200) first: if it commits per call, wrap the four steps so a failure on
step 4 rolls back step 2 (it must not leave a job approved without a submit). Route: Companion
origin required (`is_companion`), body `{confirmation: {host: str≤255, path: str≤2048, text: str≤300}}`,
`TransitionError` → 409.

**Step 4: Run** the new test plus `tests/test_proposal_state_machine.py tests/test_proposals_router.py -q`.
Expected: PASS.

**Step 5: Commit** `feat(proposals): the Companion records a submit the user made`.

### Task 10: Run log counts and the run log from the Companion

**Files:**
- Modify: `backend/app/schemas/agent_runs.py` (`RunCountKey`)
- Test: `backend/tests/test_agent_runs_router.py`

**Step 1: Failing test:** posting `{"automation": "queued-run", "outcome": "ok", "counts": {"ready": 3,
"needs_you": 1, "applied": 2, "stopped": 0, "median_run_s": 140, "median_your_s": 25}}` with the
Companion Origin header returns 201, `agent` is None, and the counts round-trip.

**Step 2: Run.** Expected: FAIL (422 on unknown keys).

**Step 3: Implement.** Add `"ready", "applied", "stopped", "median_run_s", "median_your_s"` to
`RunCountKey`. Give `"queued-run"` the title "Queued run" wherever `AgentRunRead.title` resolves names
outside the catalog (read `services/agent_runs.py`; add a small `EXTRA_TITLES = {"queued-run": "Queued run"}`
if titles come only from `automations.catalog()`). Check the web Recent runs list renders the new
count keys: find its count-word map in `frontend/` (`grep -rn needs_you frontend/components`) and
add words for each new key.

**Step 4: Run** `pytest tests/test_agent_runs_router.py tests/test_agent_runs_service.py -q`, plus the
frontend type check. Expected: PASS.

**Step 5: Commit** `feat(agent-runs): Queued run counts`.

### Task 11: "Prepare queued jobs" (optional, off by default)

**Files:**
- Modify: `backend/app/schemas/auto_apply.py` (`prepare_queued: StrictBool = False`)
- Create: `backend/app/services/prepare_queued.py`
- Modify: `backend/app/routers/proposals.py` (the PATCH and bulk-transition routes schedule it on → `accepted`)
- Modify: `frontend/components/settings/auto-apply-section.tsx` (one switch, "Prepare queued jobs":
  "Quick-tailors and makes a PDF as soon as you queue a job. Skips jobs you are tailoring yourself.")
- Test: `backend/tests/test_prepare_queued.py` (new)

**Step 1: Failing tests:**
- setting off → queueing calls nothing (monkeypatch `quick_tailor.run_for_job` to record calls);
- setting on → queueing a job with no application and a best base calls `run_for_job(job_id, base)`,
  then `application_render.render_resume`;
- **never** called when the job has an open `TailoringSession` for that base, or an application
  whose `customized_json is not None` (the "don't sabotage custom tailoring" rule; one test each);
- a failure inside prepare is logged and never fails the PATCH (the user's queue action wins).

**Step 2: Run.** Expected: FAIL.

**Step 3: Implement** `prepare_queued.run(job_id: UUID) -> None` with its own `SessionLocal`
session (it runs after the response, via FastAPI `BackgroundTasks`). Inside: re-read the setting;
resolve the base the same way Task 8 does (move that helper into `queue_run.py` and import it);
skip on open session or draft; `quick_tailor.run_for_job(...)`; render. Catch `InProgressSessionError`
and `HealthGateBlockedError`, plus `Exception` with `logger.exception`. Respect the sync write guard:
call `require_owned` before writing, as other job writers do (SYSTEM.md inv-flush-guard).

**Step 4: Run** the new test plus `tests/test_proposals_router.py tests/test_full_automation_setting.py -q`.
Expected: PASS.

**Step 5: Commit** `feat(auto-apply): optionally prepare a job's resume when it is queued`.

---

### Task 10a: The known-gaps list (backend)

Design Part 8.3. A durable, value-free table of widgets the engine could not operate or that the
user fixed by hand, so the engine's to-do list survives the 50-run trace limit.

**Files:**
- Create: `backend/app/models/autofill_widget_gap.py` (+ export from `models/__init__.py`)
- Create: `backend/migrations/versions/<12-hex>_autofill_widget_gaps.py` (down_revision = Task 6's
  revision; table-creation style: `7d3c1a9e5b20_agent_runs.py`)
- Modify: `backend/app/routers/autofill.py` (`POST /api/autofill/gaps`, `GET /api/autofill/gaps/summary`;
  `DELETE /api/autofill/telemetry` also clears gaps)
- Modify: `backend/scripts/fill_trace.py` (a `gaps` section in `report`)
- Modify: `frontend/components/settings/autofill-section.tsx` (one diagnostics line under Form filling:
  "Fields the Companion couldn't operate: N kinds on M sites, N fixed by you." with no list of hosts
  in the UI)
- Test: `backend/tests/test_autofill_gaps.py` (new)

**Row:** `family` (`^[0-9a-z]{1,24}$`, the recipe-book family hash from `content/recipes.js`), `host`
(≤253), `shape` (the inventory shape enum), `outcome` (`cannot_operate | unconfirmed | user_fixed |
missing_technique`), `count`, `first_seen_at`, `last_seen_at`; unique on (family, host, shape, outcome).
No label, no value, no option text.

**Step 1: Failing tests:**
- POST `{gaps: [{family, host, shape, outcome}]}` upserts and increments `count`, with
  `extra="forbid"` (a `label` or `value` key → 422);
- at most 40 gaps per post;
- the summary ranks by `count` desc and returns `{kinds, sites, user_fixed, top: [{family, shape,
  outcome, count, sites}]}` (no hosts in `top`);
- `DELETE /api/autofill/telemetry` clears the table (it holds hosts, like telemetry rows);
- `fill_trace.py report` prints the gaps section from a DB copy (reuse its existing DB-copy loader).

**Step 2: Run.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run**
`pytest tests/test_autofill_gaps.py tests/test_autofill_telemetry*.py -q`. Expected: PASS.

**Step 5: Commit** `feat(autofill): a durable, value-free known-gaps list`.

## Phase 2 — Page operations (content world)

### Task 12: Pure page-kind classifier and button sort

Pure functions on a plain-data page summary, so node can test them without a DOM.

**Files:**
- Create: `extension/shared/page-kind.js`
- Modify: `extension/manifest.json` (content_scripts: after `shared/profile-fields.js`)
- Modify: `extension/panel/panel.html` (after `../shared/profile-fields.js`; the runner reads the
  verdict words too)
- Modify: the roster pins listed under Conventions
- Test: `backend/tests/test_extension_page_kind.py` (new; node harness)

**The summary** (built by Task 13 in the page):
`{ host, buttons: [{id, text, type, marker, inForm, visible, enabled}], passwordInputs, confirmPassword,
otpInput, captchaVisible, fillable, hasApplyLink, confirmationText, closedText, boardNative }`

**Step 1: Write failing node tests**, modelled on `tests/test_extension_never_submits.py`: build the
source with `run_node(driver, spec, tmp_path, source=<page-kind.js text>)` and a driver that calls
`ns.pageKind.classify(spec.summary)` and `ns.pageKind.sortButton(spec.button)`. Cases:

```python
NEXT = ["Next", "Continue", "Save and Continue", "Save & continue", "Review", "Review application", "Proceed", "Next step"]
SUBMIT = ["Submit", "Submit application", "Apply", "Apply now", "Send application", "Finish", "Complete",
          "Confirm", "Continue to submit", "Submit and continue", "Review and submit"]

@pytest.mark.parametrize("text", NEXT)
def test_next_words_sort_as_next(tmp_path, text): assert sort(tmp_path, text) == "next"

@pytest.mark.parametrize("text", SUBMIT)
def test_any_submit_word_sorts_as_submit_even_beside_a_next_word(tmp_path, text):
    assert sort(tmp_path, text) == "submit"

def test_a_submit_type_button_is_submit_whatever_its_text(tmp_path):
    assert sort(tmp_path, "Next", type="submit") == "next"  # see the note below

def test_unlabelled_or_odd_buttons_are_unclear(tmp_path):
    for text in ["", "Go", "Save", "Done", "→"]:
        assert sort(tmp_path, text) == "unclear"
```

Note on `type=submit`: many wizards use `<button type="submit">Next</button>` for every step
(`tests/browser/test_inventory.py:24` has one). The text decides; `type` alone does not make it
Submit. Any SUBMIT word always wins.

Page kinds, one test each: `captcha` (captchaVisible) beats everything; `create_account`
(confirmPassword); `verify` (otpInput); `sign_in` (exactly one password input, no confirm);
`confirmation` (confirmationText and fillable == 0); `closed`; `posting` (hasApplyLink and
fillable == 0); `form_step` (fillable > 0 and a visible enabled `next` button in the form and no
visible `submit` button); `review` (a visible `submit` button and no `next` one, or an `unclear`
advance button only); `unknown` (nothing). Also: `boardNative` + posting → `board_native`.

**Step 2: Run** `pytest tests/test_extension_page_kind.py -v`. Expected: FAIL (file missing).

**Step 3: Implement:**

```js
/* Maestro CS Companion — what kind of page is this, and which button moves on?
 *
 * Pure: reads a plain summary built in the page (content/page-reader.js), never the DOM, so
 * node tests it and both worlds load it. THE ONE RULE: a button carrying any Submit word is
 * "submit" and is never clicked, at any setting; when in doubt the page is "review" and the
 * queued run stops there (docs/plans/2026-10-10-companion-queued-run-design.md Part 2).
 *
 * WHAT THIS FILE PUBLISHES: ns.pageKind = { classify, sortButton, KINDS }.
 */
(() => {
  const ns = (window.careerStudioCompanion ??= {});
  const SUBMIT_WORDS = /\b(submit|apply|send|finish|complete|confirm)\b/i;
  const NEXT_WORDS = /^\s*(next( step)?|continue|save\s*(and|&)\s*continue|proceed|review( (application|your application))?)\s*$/i;

  function sortButton({ text = "" } = {}) {
    const words = String(text).replace(/\s+/g, " ").trim();
    if (SUBMIT_WORDS.test(words)) return "submit";
    if (NEXT_WORDS.test(words)) return "next";
    return "unclear";
  }

  function classify(s) {
    if (s.captchaVisible) return { kind: "captcha" };
    if (s.confirmPassword) return { kind: "create_account" };
    if (s.otpInput) return { kind: "verify" };
    if (s.passwordInputs === 1) return { kind: "sign_in" };
    if (s.confirmationText && !s.fillable) return { kind: "confirmation" };
    if (s.closedText) return { kind: "closed" };
    if (s.hasApplyLink && !s.fillable) return { kind: s.boardNative ? "board_native" : "posting" };
    const live = (s.buttons ?? []).filter((b) => b.visible && b.enabled);
    const sorted = live.map((b) => ({ ...b, sort: b.known ?? sortButton(b) }));
    const submit = sorted.some((b) => b.sort === "submit");
    const next = sorted.filter((b) => b.sort === "next" && b.inForm !== false);
    if (s.fillable > 0 || next.length || submit) {
      if (!submit && next.length === 1) return { kind: "form_step", next: next[0].id };
      return { kind: "review" };   // a Submit, several Nexts, or nothing sure: stop
    }
    return { kind: "unknown" };
  }

  ns.pageKind = { classify, sortButton, KINDS: ["captcha", "create_account", "verify", "sign_in",
    "confirmation", "closed", "posting", "board_native", "form_step", "review", "unknown"] };
})();
```

`b.known` is filled by Task 14's table. When it says `"submit"` or `"next"` for a marker, that beats
the text sort, except that a SUBMIT word in the text still forces `"submit"`. Add that rule and its
test in Task 14.

**Step 4: Run** the new tests plus the roster pins (`tests/test_extension_manifest.py
tests/test_extension_panel.py tests/test_extension_policy_blocked.py tests/test_extension_detection.py -q`).
Expected: PASS.

**Step 5: Commit** `feat(companion): a pure page-kind classifier where any Submit word stops the run`.

### Task 13: Read the page, and click Next (content script)

**Files:**
- Create: `extension/content/page-reader.js` (DOM → summary, and the advance op)
- Modify: `extension/content/agent.js` (`PAGE_HANDLERS`: `page_classify`, `page_advance`)
- Modify: `extension/sw.js` (`BROADCASTABLE` += `"page_classify", "page_advance"`)
- Modify: `extension/manifest.json` (after `content/detect.js`, before `content/agent.js`), and the pins
- Create: `backend/tests/fixtures/browser/wizard_step1.html`, `wizard_step2.html`, `wizard_review.html`,
  `wizard_confirmation.html`, `signin.html`, `create_account.html`, `verify_code.html`, `captcha.html`,
  `posting_external.html`, `posting_board_native.html`, `closed.html`. Follow the fixture rule in
  `backend/tests/fixtures/browser/README.md`; made-up employer "Acme".
- Test: `backend/tests/browser/test_page_reader.py` (new), `backend/tests/test_extension_sw_router.py`
  (update the pinned list)

**Step 1: Write failing browser tests** (`load(page, fixture_html(...), sources=ENGINE_SOURCES +
["shared/page-kind.js", "content/page-reader.js"])`). Call
`window.careerStudioCompanion.pageReader.summary()` and `pageKind.classify(...)`:
- each fixture classifies to its kind;
- `wizard_step1.html` (a required text field plus `<button type="submit">Next</button>`) →
  `form_step`, and `pageReader.advance(next)` clicks it: the fixture's `window.__oracle.advanced`
  becomes true;
- **never-submit pin:** for every SUBMIT word from Task 12, a fixture page built inline
  (`page.set_content`) with that one button: `advance()` with any id returns
  `{clicked: false, why: "submit"}` and the button's click listener never fires;
- `advance(id)` refuses an id that is not a currently visible, enabled `next` button
  (`{clicked: false, why: "not_next"}`), so a stale id from the panel cannot click anything else;
- `advance` re-sorts the button's CURRENT text at click time (a page that relabels Next to Submit
  between classify and advance is refused);
- an invisible reCAPTCHA badge alone (`.grecaptcha-badge`) is not `captchaVisible`; a visible
  challenge iframe (`iframe[src*="recaptcha/api2/bframe"]`, `iframe[src*="hcaptcha.com"]` with a box
  over 50×50 px, `iframe[src*="challenges.cloudflare.com"]`) is.

**Step 2: Run** `pytest tests/browser/test_page_reader.py -v`. Expected: FAIL.

**Step 3: Implement** `content/page-reader.js`, publishing `ns.pageReader = { summary, advance }`:
- `summary()` collects buttons from `button, input[type=submit], input[type=button], a[role=button]`.
  Use `ns.fillableControls()` for `fillable` (from detect.js). `visible` means a non-zero rect and not
  `visibility:hidden`. `inForm` means inside a `form`, `[role=form]`, or the same container as the
  first fillable control. Give each button a stable `id` per call: set
  `data-maestro-btn="<n>"` on it, the same tagging idea as `data-rt-qid`.
  - Texts: `textContent` / `value` / `aria-label`, trimmed, ≤80 chars.
  - `confirmationText`: `/thank you for (applying|your application)|application (was )?(submitted|received)|we('ve| have) received your application/i`
    on `document.body.innerText`, sliced to 20k.
  - `closedText`: `/no longer (accepting applications|available)|this (job|position) (has been|is) (closed|filled)/i`.
  - `hasApplyLink`: a visible link or button whose text matches `/^\s*apply( now| for this job)?\s*$/i`.
  - `boardNative`: `location.hostname` ends with `linkedin.com` or `indeed.com`.
  - Never read input VALUES (inv-autofill-telemetry-no-values spirit). The summary holds counts,
    button texts and booleans.
- `advance(id)`: find `[data-maestro-btn="<id>"]`, re-run `ns.pageKind.sortButton` on its current
  text, refuse unless it is `"next"`, visible and enabled, then `fillBase`-style click (reuse
  `ns.fillBase` click helpers if they exist; else `el.click()`). Return `{clicked: true}`.
- `agent.js` handlers, both GATED with `frameMayReceiveUserData(msg)` like `fill_inventory`. The
  refused shape is `{summary: null}` / `{clicked: false, why: "gate"}`:
  ```js
  page_classify: (msg) => (frameMayReceiveUserData(msg)
    ? { summary: ns.pageReader.summary(), top: window.top === window.self }
    : { summary: null }),
  page_advance: (msg) => (frameMayReceiveUserData(msg)
    ? ns.pageReader.advance(msg.id)
    : { clicked: false, why: "gate" }),
  ```
- Update `policy.js`'s header comment: Next is now clicked only by `page_advance` during a queued
  run; Submit never. Leave the deny-lists themselves unchanged.
- Keep `content/sections.js` `NAV_ID` and `fill-core.js` `keyable` unchanged. The fill engine still
  never touches navigation; only `page_advance` does.

**Step 4: Run** `pytest tests/browser/test_page_reader.py tests/test_extension_sw_router.py
tests/test_extension_manifest.py tests/test_extension_never_submits.py tests/browser/test_inventory.py -q`.
Expected: PASS.

**Step 5: Commit** `feat(companion): read the page kind and click Next, never Submit`.

### Task 14: Known Next/Submit buttons per ATS

**Files:**
- Create: `extension/shared/ats-buttons.js` (`ns.atsButtons = { knownSort(host, buttonAttrs) }`)
- Modify: `extension/content/page-reader.js` (`summary()` sets `known` on each button)
- Modify: `extension/shared/page-kind.js` (a SUBMIT word still beats `known: "next"`)
- Modify: manifest + pins (before `shared/page-kind.js`)
- Test: `backend/tests/test_extension_ats_buttons.py` (node) and cases in `tests/browser/test_page_reader.py`

**Step 1: Find real markers, don't guess.** Grep the existing fixtures for footer and advance buttons:
`grep -l "data-automation-id" backend/tests/fixtures/browser/*.html` and read the button markup in
the Workday and iCIMS fixtures. Add a table entry ONLY for a marker you can see in a fixture or a
recorded page, and cite it in a comment. Expected starting entries (verify each one):
- Workday: the footer advance button's `data-automation-id` (its text reads "Next" on steps, then
  "Submit" on review, so the entry marks it as THE advance control and lets the text decide; it
  never maps it to `"next"` unconditionally);
- Greenhouse: `#submit_app` → `"submit"`;
- Lever: `button[data-qa="btn-submit"]` or `#btn-submit` → `"submit"`;
- Ashby and iCIMS: only if a fixture shows them.

Untabled hosts use the generic sort.

**Step 2: Failing tests:**
- Greenhouse's submit marker with the odd text "Done" → `submit`;
- a SUBMIT word on a `known: "next"` marker → `submit`;
- an unknown host → no `known` key.

**Step 3: Implement**, with entries keyed by the `ATS_HOSTS` vendor regexes from `content/detect.js`.
Copy the regexes rather than importing them (detect.js publishes no table), and add a test that the
two tables list the same hosts for the vendors present.

**Step 4: Run** the new and existing page-reader tests. Expected: PASS.

**Step 5: Commit** `feat(companion): known ATS advance buttons`.

### Task 15: Sign in with the saved login (top frame only)

**Files:**
- Modify: `extension/content/page-reader.js` (`signIn({email, password, allowedHost})`)
- Modify: `extension/content/agent.js` (`page_sign_in` handler)
- Modify: `extension/sw.js` (`panel_frame0` `ALLOWED` += `"page_sign_in"`; NOT `BROADCASTABLE`)
- Test: `backend/tests/browser/test_page_reader.py`, `backend/tests/test_extension_sw_router.py`

**Step 1: Failing tests:**
- `signin.html`: `signIn({email, password, allowedHost: "x.test"})` on a page whose host is `x.test`
  types both values (assert through the oracle), clicks the sign-in button, returns `{signedIn: "clicked"}`;
- a host mismatch returns `{signedIn: false, why: "host"}` and types nothing;
- in a subframe (`window.top !== window.self`) it returns `{signedIn: false, why: "frame"}`;
- a page with a confirm-password field returns `{signedIn: false, why: "create_account"}`;
- the sw router test: `page_sign_in` is accepted through `panel_frame0` and refused through
  `page_broadcast`.
- **password pin:** with `consentForms: true` (which lifts every label refusal), `fill_inventory`
  on a page holding `input[type=password]` and `input[autocomplete=new-password]` never lists either
  (browser test on `signin.html` and `create_account.html`). If the inventory lists one today, exclude
  `type=password` in `content/inventory.js` by SHAPE, not by label, so no consent setting can lift it.

**Step 2: Run.** Expected: FAIL.

**Step 3: Implement.** Find the email box by `input[type=email]`, else
`input[autocomplete=username]`, else a text input whose label matches `/e-?mail|user ?name/i`; find
the password box by `input[type=password]`. Type through `ns.fillBase` (`visitControl`/`leaveControl`
wrappers so React and Workday see real input). Find the button with
`/^\s*(sign in|log in|login|continue|next)\s*$/i`. Pressing "Continue" on a sign-in page is allowed:
it is not a submit of the application. Never keep the values in any variable outside the call, and
never log them. The handler:

```js
page_sign_in: (msg) => (window.top === window.self
  ? ns.pageReader.signIn({ email: msg.email, password: msg.password, allowedHost: msg.allowedHost })
  : { signedIn: false, why: "frame" }),
```

**Step 4: Run** the tests. Expected: PASS.

**Step 5: Commit** `feat(companion): sign in with the saved job-site login, top frame only`.

### Task 16: Save job records the employer's apply link

**Files:**
- Modify: `extension/content/agent.js` (`extractJobPosting` result gains `applyUrl`)
- Modify: `extension/panel/panel.js` (`ingestBodyFrom` ~1342 and its "TWO KEYS AND NO MORE" comment;
  `job.js:94` posts the body)
- Test: `backend/tests/test_extension_panel_job.py`, `backend/tests/test_extension_posting_identity.py`
  or a new node test for the extraction

**Step 1: Failing tests:**
- a JSON-LD `JobPosting` with `"url"` pointing off the board, or a visible "Apply on company site"
  link with an absolute `https://` href to another host → `applyUrl` is that href;
- a LinkedIn/Indeed posting whose apply link stays on the board → `applyUrl` null;
- `javascript:` and relative hrefs → null;
- the panel's POST `/api/jobs` body carries `apply_url` when present and omits the key otherwise
  (assert through the panel harness `spec.api` capture, as the existing Save job tests do).

**Step 2: Run.** Expected: FAIL.

**Step 3: Implement.** Read only the page the user is viewing. Never `fetch` a board page in the
background with the user's cookies. Rewrite the `ingestBodyFrom` comment to "THREE KEYS AND NO MORE"
and say why the third exists.

**Step 4: Run** `pytest tests/test_extension_panel_job.py -q` and the new test. Expected: PASS.

**Step 5: Commit** `feat(companion): Save job keeps the employer's apply link`.

---

## Phase 3 — The runner and the panel

### Task 17: `shared/queue-run.js`, the runner (pure, deps injected)

The heart of the feature. One run record per job; a pure async driver; every browser and panel
effect injected, so node can test the whole state machine with fakes.

**Files:**
- Create: `extension/shared/queue-run.js` (panel-only; add to `panel.html` after
  `../shared/fill-loop.js`'s block, keeping `fill-loop.js` directly after `guided-run.js`)
- Modify: `backend/tests/test_extension_page_messaging.py` (add the new shared caller file)
- Test: `backend/tests/test_extension_queue_run.py` (new; node harness with fake deps, like
  `test_extension_guided_fill.py`'s `_RUNNER_DRIVER`)

**Record shape** (plain JSON; saved at every page boundary):

```js
{ jobId, proposalId, applicationId, base, company, title, openUrl,
  tabId: null, hosts: [], step: 0, fingerprint: null, seen: [],
  status: "waiting" | "opening" | "signing_in" | "filling" | "advancing" | "parked" | "ready"
        | "confirming" | "applied" | "stopped",
  why: null,              // a short reason word when parked/stopped: captcha | create_account | verify
                          // | sign_in_failed | board_native | closed | open_questions | no_change
                          // | loop | step_limit | error | user
  open: [],               // parked page's open questions: [{fid, question, required}]
  startedAt, readyAt, yourMs: 0, flowOrigin: null }
```

**Deps** (all required except where noted; throw `TypeError("queue-run: missing dep X")` like
fill-loop):
`openTab(url) → tabId`, `classify(tabId) → [{frameId, result:{summary, top}}]`,
`advance(tabId, frameId, id) → {clicked, why}`, `fill(record) → fill-loop report`, `signIn(record) →
{signedIn, why}`, `waitForChange(tabId, before, ms) → {changed, url}`, `save(records)`,
`onUpdate(record)`, `now()`, `sleep(ms)`, `random()`, `cancelled(record)`.

**Exports:** `ns.queueRun = { drive(record, deps), next(records, limits), LIMITS }`, where `LIMITS =
{ parallel: 3, waitingMax: 10, stepMax: 15, changeMs: 15000, paceMs: [2000, 6000] }`.

**`drive(record, deps)`, the per-tab loop:**
1. `opening`: `tabId = openTab(openUrl)`.
2. Loop until a terminal state, checking `cancelled(record)` before every step:
   - `classify` → the first frame result whose `summary` is not null, preferring `top: true`, then
     the frame with the most `fillable`. `kind = ns.pageKind.classify(summary)`.
   - `captcha | create_account | verify | board_native` → `parked` with `why = kind`. Return.
   - `closed` → `stopped`, `why: "closed"`. Return.
   - `confirmation` → only after `ready`: status `confirming` (Task 20 records it). Before ready,
     treat as `review`.
   - `sign_in` → if `record.signedIn` is already true → `parked` `why: "sign_in_failed"` (no
     retry, to avoid a lockout). Else `signing_in`, `signIn(record)`, set `record.signedIn = true`,
     `waitForChange`, continue.
   - `posting` → click its Apply through `advance`. "Apply" is a Submit word, so this is the ONE
     narrow exception, and it holds only when ALL of these are true: `classify` said `posting`
     (no fillable control on the page), `record.step === 0`, nothing has been filled in this
     record yet (`record.filled !== true`), and the tab's host is `openUrl`'s host. Add
     `advance(tabId, frameId, id, {apply: true})` to Task 13's `advance`: it re-checks on the page
     that no fillable control exists and that the text matches the apply-link regex
     `/^\s*apply( now| for this job)?\s*$/i` exactly. Add those cases to Task 13's tests while
     implementing here (log the deviation): including a review-like page with no fields and an
     "Apply" button reached AFTER a fill, which must NOT be clicked (the record's `filled` flag
     makes it `review`). Then `waitForChange`, continue. Set `record.filled = true` after any
     `fill(record)`.
   - `form_step` or `review` → `filling`: `report = await fill(record)`. Open questions =
     `report.fields.filter(f => f.required && ["needs_answer","cannot_operate"].includes(f.status))`.
     `unconfirmed` does NOT park (owner, 2026-10-11, "park less"): the advance below and the page's
     own validation decide, and the no-change path refills once before parking.
     If any → `parked`, `why: "open_questions"`, `open = [...]`. Return (Resume re-enters `drive`).
   - After a clean fill: `review` → `ready`, `readyAt = now()`. Return. `form_step` → `advancing`:
     record the fingerprint (url + step heading + sorted button texts + fillable count, joined); if
     it is already in `seen` → `parked`, `why: "loop"`; if `step >= stepMax` → `parked`,
     `why: "step_limit"`; else `advance(...)`, `waitForChange(tabId, fingerprint, changeMs)`. If not
     changed → `fill(record)` once more (the page may show errors the fill can fix), advance once
     more; still unchanged → `parked`, `why: "no_change"`. Else `step += 1`, continue.
   - `unknown` → `parked`, `why: "unknown_page"`.
3. Every status change calls `onUpdate(record)` then `save(records)`.

**`next(records, limits)`:** the scheduler.
- Count `filling`-like statuses (`opening | signing_in | filling | advancing`), and waiting
  `parked | ready`.
- Return the next `waiting` record when filling < parallel and waiting < waitingMax, else null.
- The host sleeps `paceMs` (random in range) between opens.

**Step 1: Failing node tests**, one per transition, with scripted fakes (`classify` returns a queue
of summaries, `fill` a queue of reports):
- step1 → step2 → review ends `ready` after two advances, no submit ever requested (assert that the
  `advance` fake only ever saw ids the summary marked `next`);
- a review page with a Submit button ends `ready` without calling `advance`;
- a captcha → `parked/captcha`; a create-account page → `parked/create_account`;
- sign_in → signIn called once → still sign_in → `parked/sign_in_failed` (signIn not called twice);
- an open required field → `parked/open_questions` with `open` listing it;
- a required field reported `unconfirmed` does NOT park: the driver advances; if the page then does
  not change, it refills once and parks with `why: "no_change"`;
- the same fingerprint twice → `parked/loop`; no change after advance + refill → `parked/no_change`;
- 15 steps → `parked/step_limit`;
- `cancelled()` true mid-run → `stopped/user`, and no deps are called after it;
- `next()`: 3 filling → null; 10 parked/ready → null; else the oldest waiting.

**Step 2: Run** `pytest tests/test_extension_queue_run.py -v`. Expected: FAIL.

**Step 3: Implement** to pass. Keep it free of `chrome.*`, `document` and `fetch`
(`test_extension_panel.py` forbids `fetch(` in panel files).

**Step 4: Run** the tests plus `tests/test_extension_panel.py tests/test_extension_page_messaging.py -q`.
Expected: PASS.

**Step 5: Commit** `feat(companion): the queued-run driver`.

### Task 18: Per-tab job memory (replaces the 30-minute single key)

**Files:**
- Modify: `extension/shared/decisions.js:334` (`restorableSession`: no `ttlMs`; origin, tenant and
  backend-wins guards stay)
- Modify: `extension/panel/panel.js` (`KEY`, `SESSION_TTL_MS`, `rememberSession` ~2139,
  `restoreSession` ~3127, `refreshPage` comment ~2860, the KEY comment block ~140-171)
- Modify: `extension/INTERNALS.md` (the `widget.session` paragraphs ~1511 and ~451-462)
- Test: `backend/tests/test_extension_panel*.py` and `tests/extension_fixtures.py` (session fixtures),
  plus the decisions tests that pass `ttlMs`

**Behaviour:**
- Entries live in `chrome.storage.session` under `widget.sessions`, a map `{[tabId]: entry}`.
  `rememberSession` writes `card.tabId`'s entry; `restoreSession` reads `card.tabId`'s.
- `chrome.tabs.onRemoved` deletes the entry.
- Migration: on panel boot, read the old `chrome.storage.local["widget.session"]` once. If it is
  under 30 minutes old, adopt it for the active tab (all guards still apply). Then
  `chrome.storage.local.remove("widget.session")`. Comment why the rename is safe now.
- No TTL: an entry lives while its tab is open. The tenant and backend-wins guards still drop it
  when the tab moves to another employer or the server matches a different job.

**Step 1: Failing tests** (panel harness; the stub already has `storage.session`):
- a pick made in tab 1, then 40 minutes later (inject `Date.now`), a wizard page in tab 1 restores it;
- a pick in tab 2 does not overwrite tab 1's;
- closing tab 1 removes its entry;
- an old `widget.session` local entry is migrated once and removed;
- a tenant change in the same tab still drops it (existing guard tests keep passing).

**Step 2: Run.** Expected: FAIL.

**Step 3: Implement.** Update every `restorableSession` caller and test that passed `ttlMs`.

**Step 4: Run** `pytest tests/test_extension_panel*.py tests/test_extension_posting_identity.py -q`.
Expected: PASS.

**Step 5: Commit** `feat(companion): remember each tab's job while the tab is open`.

### Task 18a: Per-run fill limits ("try harder in runs")

Design Part 8.1. Today `ns.fillLoop.limits` is one shared mutable object (tests reassign it).
Parallel runs and the user's own Autofill must not share a changed budget.

**Files:**
- Modify: `extension/shared/fill-loop.js` (`runFill(deps, options)`: `options.limits` merged over the
  module defaults for THIS run only; every read of `limits.X` inside the run reads the merged copy)
- Test: `backend/tests/browser/test_fill_loop.py`

**Step 1: Failing tests:**
- a run with `options.limits = {FIELD_MS: 60000, MAX_STEPS: 12}` lets a scripted field take 10
  adaptive steps where the default run gives up at 8;
- a second run started without `options.limits` in the same page still stops at 8 (the module
  object is unchanged after the first run).

**Step 2: Run** `pytest tests/browser/test_fill_loop.py -k limits -v`. Expected: FAIL.

**Step 3: Implement.** Add `const RUN_LIMITS = {FIELD_MS: 60000, MAX_STEPS: 12}` to
`shared/queue-run.js` (Task 17); Task 19's `fill` dep passes it.

**Step 4: Run** the whole `tests/browser/test_fill_loop.py`. Expected: PASS.

**Step 5: Commit** `feat(fill-loop): per-run limits; queued runs try harder`.

### Task 19: Panel host: the Queue view, start a run, tabs, sign-in, stop

**Files:**
- Create: `extension/panel/actions/run.js` (`ns.panelActionsRun = { loadQueue, startRun, stopRun,
  stopAll, resumeRun, focusRun, setRunChoice, setView }`)
- Modify: `extension/panel/actions.js` (roster: `need("actions/run.js", ns.panelActionsRun)` plus the
  bindings), `extension/panel/panel.html` (after `actions/track.js`)
- Modify: `extension/panel/panel.js`:
  - new `card` fields `view: "page" | "queue"`, `queue: null`, `runs: []`, `runWindowId: null`,
    `runParallel: 3`;
  - `render()` draws the **This page | Queue (N)** switch at the top (a tablist; N = queued count)
    and either today's rail (`page`) or the Queue view (`queue`, Task 20's `panel/runs.js`);
  - fresh-open view rule: `view = "queue"` when a run is active or the active tab has neither a
    matched job nor a detected form; otherwise `"page"`. A user switch sticks until the panel closes;
  - `actionStore()` gains `tabBroadcast(tabId, message)` = `ask("page_broadcast", {tabId, message})`
    and `tabFrame0(tabId, message)`;
  - `withFlowOrigin` reads a run record's `flowOrigin` when `payload.tabId` belongs to a run (the
    current `flowFor` returns null unless `tabId === card.tabId`);
  - `onTab` must NOT send `fill_cancel` to a run tab, and must not cancel a run when the user
    switches tabs.
- Test: `backend/tests/test_extension_panel_run.py` (new; panel harness)

**Behaviour:**
- **The Queue view** shows only when `GET /api/settings/auto-apply` says `full_automation` (else
  the switch is hidden and the panel is today's). Opening it → `loadQueue` →
  `GET /api/autofill/queue-run` → `card.queue = {items, skipped, cap_left, choices}`, re-read on
  every switch to Queue and after every run. The list itself is the pre-run step; there is no
  separate sheet. **Apply to N** (N = ticked rows) starts the run.
- **Per-row choices on the Queue view:**
  - `knockout === "conflict"` → unticked, "Apply anyway" toggle;
  - `board_native` → listed, unticked, "Opens on the job board; apply there yourself";
  - `pdf_ready` → nothing to choose;
  - `!pdf_ready && has_draft` → "Create PDF" (render the existing draft; never quick tailor);
  - `!pdf_ready && !has_draft && tailoring_open` → default **Skip** ("You're tailoring this one;
    finish it first"), with no quick tailor option;
  - `!pdf_ready && !has_draft && !tailoring_open && base_resume` → **Quick tailor** (default) |
    Use base resume | Skip;
  - `!base_resume` → Skip ("Score this job first").
  - `knockout === "incomplete_profile"` → one line per kind with a link to the web app's profile
    settings (`card.settings` holds the web app URL; reuse the deep-link builder in `renderIdentity`).
- **startRun:**
  - Records are built from ticked items.
  - Window: `chrome.windows.create({url: "about:blank", focused: false})` → `runWindowId`.
  - Resume prep: for items needing it, run quick tailor (`POST /api/jobs/{id}/quick-tailor {base_resume}`)
    or render (`POST /api/applications/{id}/render`) ONE AT A TIME, sequentially in the background,
    each before its record becomes `waiting`. Use base resume → `POST /api/applications/from-base`
    `{job_id, base_resume}` then render. A 409 from quick tailor (an open session) → that record
    `stopped`, `why: "tailoring_open"`.
  - Scheduler: `while` `ns.queueRun.next(...)` yields a record → `drive(record, deps)` without
    awaiting it, then `sleep(pace)`. Re-check every 2 s and after every record update.
- **Deps for drive:**
  - `openTab`: `chrome.tabs.create({windowId, url, active: false})`. Adopt tabs opened from it:
    `chrome.tabs.onCreated` with `openerTabId === record.tabId` swaps `record.tabId`.
  - `classify`: `tabBroadcast(tabId, {type: "page_classify", flowOrigin})`.
  - `advance`: `tabBroadcast` with a frame target. Add `frameId` support to `page_broadcast`
    (`sw.js` `fanoutTab`: when `msg.frameId` is an integer, send to that frame only) and pin it in
    `test_extension_sw_router.py`.
  - `fill`: `ns.fillLoop.runFill` with
    `{broadcast: (m) => tabBroadcast(record.tabId, m), api, recipes, cancelled: () => cancelled(record), beforeSweep: autoAttach for record}`
    and `{applicationId, sourceHint}`, then `ns.panelRecordReceipt` for that record's tab. Generalize
    `recordReceipt`'s `store`/`facts` reads to take `{tabId, jobId, applicationId, base}` explicitly
    rather than `card`. Move the shared part into a helper both callers use.
  - `signIn`: `POST /api/companion/job-site-login {proposal_id}` →
    `tabFrame0(tabId, {type: "page_sign_in", email, password, allowedHost})`. The credentials live
    only in that function's locals and are never written to `card`, a record, a trace or a note.
    `allowedHost` is the tab's current host and must be in `record.hosts` (every host the run reached
    from `openUrl` by its own Apply/Next clicks, appended after each `waitForChange`).
  - `waitForChange`: poll `chrome.tabs.get(tabId).url` plus `page_classify` every 500 ms up to `ms`,
    comparing fingerprints.
  - `save`: `chrome.storage.session.set({queueRun: records})`. On panel boot, read it and show the
    records (status only; a reopened panel does not resume driving, per the design).
  - `cancelled`: `record.stopRequested || card.runStopAll`.
- **Spike result (Task 1):** if it says "take turns", `fill` first calls
  `chrome.tabs.update(record.tabId, {active: true})` inside the run window and the scheduler keeps
  `parallel` at 1. Follow the recorded rule.
- **stopRun / stopAll:** set flags, send `fill_cancel` to the record's tab.

**Step 1: Failing panel-harness tests.** Extend the stub with `windows.create`, `tabs.create`,
`tabs.onCreated`, `tabs.onRemoved` and `tabs.update`, recording calls:
- the button is hidden when full automation is off;
- the switch is hidden with full automation off; the fresh-open rule picks `queue` on a page with no job
  and no form, and `page` on a posting; the Queue view's defaults: a conflict is unticked; a tailoring-open job defaults to Skip with no quick
  tailor offered; a draft offers only Create PDF;
- startRun opens ONE window, never focuses it, and opens at most 3 tabs with gaps
  (`sleep` fake records the pace);
- `quick-tailor` is never called for a job with `tailoring_open` or `has_draft`, whatever the user
  clicked (guard inside `startRun`, not only in the Queue view's rows);
- sign-in: the credentials never appear in `card`, `sessionWrites`, `writes`, telemetry or trace
  sends (scan every recorded message for the password string);
- a tab switch during a run sends no `fill_cancel` to run tabs;
- stopAll sends `fill_cancel` to every running tab.

**Step 2: Run.** Expected: FAIL.

**Step 3: Implement.**

**Step 4: Run** `pytest tests/test_extension_panel*.py tests/test_extension_sw_router.py
tests/test_extension_queue_run.py -q`. Expected: PASS.

**Step 5: Commit** `feat(companion): the Queue view, run window, pacing, sign-in`.

### Task 20: Run list, Needs you, Resume, auto-mark applied, run log

**Files:**
- Create: `extension/panel/runs.js` (`ns.panelRuns = { render(ctx) }`; add to `panel.html` before
  `panel.js`)
- Modify: `extension/panel/panel.html` (a `<section id="runs">` region between `#identity` and `#rail`)
- Modify: `extension/panel/panel.js` (`render()` calls `region("runs").replaceChildren(ns.panelRuns.render(...))`)
- Modify: `extension/panel/actions/run.js` (`resumeRun`, confirmation watch, undo, run log, timing)
- Modify: `extension/panel/panel.css` (follow the existing tokens; no emoji or text glyphs, icons via
  `ns.icon`; `test_extension_panel_icons.py` scans this)
- Test: `backend/tests/test_extension_panel_run.py`

**Behaviour:**
- **Run list** (shown when `card.runs.length`): header "Queued run · N ready · N need you" with
  **Stop all**. One row per record: company · title · status word · step. Status words: Waiting,
  Opening, Signing in, Filling page N, Needs you, Ready to submit, Marking applied, Applied,
  Stopped. A `why` word appears as a short reason ("CAPTCHA", "Create an account", "Email code",
  "Sign-in didn't work", "Apply on the job board", "Position closed", "Questions on this page",
  "The page didn't move on", "Too many steps").
- Clicking a row: `chrome.tabs.update(tabId, {active: true})` and
  `chrome.windows.update(runWindowId, {focused: true})`.
- **Needs you (open_questions):** the row expands to list `open` questions as jump links
  (`fill_focus` to that tab) and **Resume**. No checkbox: answers are always remembered.
  Resume:
  1. `fill_inventory {peek: true, readOnly: true}` on the tab, then build the receipt with
     `ns.receipt.fromEdits` (values the user typed) and post it (source `you`);
  2. `learnAnswer(store, {label: question}, answer)` for each answered open question that is not
     long free text (the `textarea` shape is never learned), using the same `saveTargetFor` path as
     pause rows (move `learnAnswer` to accept a plain label). Keep each target's PREVIOUS value; the
     panel shows "Saved N answers to your profile · Undo", and Undo writes the previous values back;
  3. re-enter `drive(record, deps)`, which refills, then advances or reaches ready.
  Walls (captcha / verify / create_account / sign_in_failed) also show **Resume** after "Do this
  step in the tab, then Resume", plus **Skip this job**: it stops that record (`why: "user"`) and
  leaves the job Queued, never skipped or rejected on the server. Below, one line names the other
  walls ("It also stops here for: a CAPTCHA · creating an account · a sign-in that didn't work ·
  apply on a job board").
- **Running card (This page, Fill):** eyebrow "Applying", title "Filling page N", a progress bar,
  a page-by-page list (finished pages with a tick and their filled count, the current page with
  "k of n"), **Stop**, and the fixed line "It clicks Next for you. It never clicks Submit." 
- **Ready to submit:** a row with "Check before you submit" flags (the receipt flags from the
  record's last receipt post). Nothing to press in the panel; the user submits in the tab.
- **Confirmation watch:** for `ready` records, `chrome.tabs.onUpdated` (url change) on that tab →
  `classify`. On `confirmation`:
  1. the row shows "Marking applied in 8 s · Undo";
  2. after 8 s with no Undo → `POST /api/proposals/{proposalId}/companion-submitted` with
     `{confirmation: {host, path, text}}`, where `text` is the matched confirmation phrase only
     (≤300 chars, never page prose beyond it) → `applied`;
  3. Undo → back to `ready`, with a manual **Mark applied** button calling the same endpoint.
  A 409 (cap or skip list) shows the server's sentence through `failureNote`.
- **Timing:** `yourMs` accrues from park to Resume and from `readyAt` to confirmation.
- **Queue view rows outside the run:** unticked jobs stay in the list as "Queued · Not in this run".
- **Run finished:** a summary card above the remaining rows: Applied, Needs you, your time per job
  (median `yourMs`), run time; **Done** posts the run log and clears the summary. Applied jobs leave
  the queue (the next `loadQueue` no longer returns them); the remaining rows keep their reason.
- **Run log:** when no record is active (all applied, parked, ready or stopped) and the user
  presses **Done** (or every record is terminal), POST `/api/agent-runs`
  `{automation: "queued-run", outcome: "ok"|"partial", counts: {ready, needs_you, applied, stopped,
  median_run_s, median_your_s}, job_ids}` once per run.

**Step 1: Failing panel-harness tests:**
- the rows render each status word;
- Resume posts a `you`-source receipt, always calls the learn path (never for a textarea), shows the
  "Saved N answers" line, Undo restores the previous profile values, then re-drives;
- confirmation → nothing posted before 8 s (fake timers) → posted after; Undo within 8 s → nothing
  posted, Mark applied shows;
- the run log posts once with medians;
- no row ever shows a Submit control;
- Skip this job leaves the proposal `accepted` (no PATCH sent);
- unticked jobs render "Not in this run"; the finished summary shows the four numbers and Done
  posts the run log once.

**Step 2: Run.** Expected: FAIL.

**Step 3: Implement.**

**Step 4: Run** `pytest tests/test_extension_panel*.py tests/test_extension_panel_icons.py -q`.
Expected: PASS.

**Step 5: Commit** `feat(companion): run list, Needs you with Resume, auto-mark after your Submit`.

### Task 20a: Job step: Apply and Queue

Design "Job step buttons". With full automation on, the Job step's footer primary becomes **Apply**,
with **Queue** beside it; with it off, **Save job** exactly as today.

**Files:**
- Modify: `extension/panel/actions/job.js` (`addJob` stays; new `queueJob`, `applyJob`)
- Modify: `extension/panel/actions.js` (roster bindings), `extension/panel/panel.js` (`renderFoot`'s
  Job-stage primary; `card.settings` must carry `full_automation`: read
  `GET /api/settings/auto-apply` in `loadContext` once per bind, or reuse Task 19's read)
- Modify: `extension/panel/actions/run.js` (`startRun` accepts a one-record run in the CURRENT tab)
- Test: `backend/tests/test_extension_panel_job.py`, `backend/tests/test_extension_panel_run.py`

**Behaviour:**
- **Queue:** `addJob` (save, extract and score as today), then `POST /api/proposals` with `{job_id,
  fit}` built like `frontend/lib/api.ts` `promoteJobToAgentQueue` (chosen base = the Job step's pick;
  scores from the panel's `card.scores`), then `PATCH /api/proposals/{id}` with
  `{status: "accepted", consent: {channel: "companion"}}` (Task 9 adds the channel). The note says
  "Queued. Apply to queued will pick it up."
- **Apply:** Queue, then resume readiness by the same rules as the Queue view's rows (Task 19)
  for this one job: a PDF is ready → go; a draft without a PDF → Create PDF; nothing tailored and no
  open custom session → Quick tailor with the picked base; an open custom session → stop with "You're
  tailoring this job in Maestro. Finish it, then press Apply." (never quick-tailor over it). Then a
  one-record run whose `openTab` reuses the current tab: when the page is a posting on a job board
  and the job has an `apply_url`, navigate this tab to it (`chrome.tabs.update(tabId, {url})`); when
  the page is already the employer's form, start at classify.
- Hidden when the job is already applied, or its proposal is already queued (then the footer shows
  **Apply** only, which skips the queue step).
- **Canvas details:** with full automation off, Save job carries one line, "Turn on full automation
  in Settings to apply from here." With it on, the base picker's card reads "Apply with this
  resume", Apply and Queue sit side by side, one line says "Apply fills every page here and stops
  before Submit.", and below are **Update scores** and **Tailor first, then apply** (opens today's
  Resume step instead of applying). The open-custom-session state ("Finish tailoring first") offers
  **Finish in Maestro CS** (the web app's tailoring session) and **Apply with the base resume**
  (`POST /api/applications/from-base` then render, then the one-job run; the open session is left
  untouched).

**Step 1: Failing panel-harness tests:**
- full automation off → only Save job;
- on → Apply primary and Queue secondary;
- Queue posts the proposal then the accepted PATCH with channel `companion`;
- Apply with an open custom session never calls quick-tailor and shows the finish-first note;
- Apply on a board posting with `apply_url` navigates the current tab and drives one record;
- "Apply with the base resume" never calls quick-tailor and leaves the open session `open`;
- no new tab or window opens for Apply.

**Step 2: Run.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run**
`pytest tests/test_extension_panel*.py -q`. Expected: PASS.

**Step 5: Commit** `feat(companion): Apply and Queue on the Job step`.

### Task 20b: Learn from the user's fix

Design Part 8.2. When a parked field is fixed by hand and the user presses Resume, teach the recipe
book the move the user used, and note techniques the engine lacks.

**Files:**
- Modify: `extension/content/recipes.js` (a gesture watcher per field: `watch(fid)` / `report(fid)`)
- Modify: `extension/content/agent.js` (`fill_watch {fids}` starts watching parked fields;
  `fill_watch_report {fids}` returns `{fid: {family, site, gestures: [...]}}`), `extension/sw.js`
  (`BROADCASTABLE` += both, pin updated)
- Modify: `extension/shared/recipe-book.js` (`recordDemonstration(lesson)`: a known move enters at
  `probation` for that site only; never `trusted` from one demonstration; a family lesson needs the
  usual two sites)
- Modify: `extension/panel/actions/run.js` (on park: `fill_watch`; on Resume: `fill_watch_report`,
  then for each field now holding a value (from the receipt's `fromEdits`): a known move →
  `recipes.recordDemonstration`; no known move → POST `/api/autofill/gaps` with
  `outcome: "missing_technique"`; every fixed field → a `user_fixed` gap; every run's leftover
  `cannot_operate`/`unconfirmed` fields → their gap rows. All gap posts go through the
  `telemetry`-gated path and are skipped when `telemetryEnabled` is false.)
- Test: `backend/tests/browser/test_recipes.py`, `backend/tests/test_extension_panel_run.py`

**Gesture vocabulary** (value-free; the engine's own axes from `recipe-book.js` `AXES`):
`open:press` (a pointer press opened it), `open:keys` (ArrowDown, Enter or Space opened it),
`search:enter` (typing followed by Enter committed it), `search:debounce` (typing, then a pause,
then a pointer pick). Record event TYPES and key NAMES from a fixed allowlist (`ArrowDown`, `ArrowUp`,
`Enter`, `Space`, `Tab`, `Escape`) only, never `key` for printable characters and never `value`.

**Step 1: Failing tests:**
- browser: on `workday_listbox.html` and `react_select.html`, simulate the user opening with the
  keyboard and picking → `report` says `open:keys`; typing "Spr" then Enter → `search:enter`; the
  report contains no typed character (assert "S", "p" and "r" never appear in the JSON);
- recipe-book (node): one demonstration → `probation` at `s:<site>`, not trusted; a second
  demonstration on another site → the family is `trusted` (same lifecycle as engine wins);
- panel: Resume after a hand fix posts a `user_fixed` gap and records the demonstration; with
  telemetry off, no gap post and no demonstration is recorded.

**Step 2: Run.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run**
`pytest tests/browser/test_recipes.py tests/test_extension_panel_run.py tests/test_extension_sw_router.py -q`.
Expected: PASS.

**Step 5: Commit** `feat(companion): a hand fix teaches the recipe book; misses join the known-gaps list`.

### Task 21 (conditional): `/api/autofill/page` for unclear buttons

Build only if the owner's live check (Task 23) shows "Ready to submit" landing on pages that are
really mid-wizard because the advance button was unclear. Shape it like `/api/autofill/step`
(`schemas/autofill_fill.py:184`):
- request `{buttons: [{id, text}], heading, step_texts}` (button texts only, no page prose);
- response `{choice: id | "none"}`.

The server refuses any id whose text the Python twin of `sortButton` calls `submit` (write that twin
and a parity test, as `test_both_copies_of_the_policy_deny_list_stay_identical` does for policy).
The runner sends only `unclear` buttons, and only when there is no `submit` button on the page.
Ask the planner before starting.

---

## Phase 4 — Rules, docs, verification

### Task 22: Change the rules on purpose, and document

**Files:**
- `SYSTEM.md`: first groom to make room (`python3 scripts/check_system_md.py` must pass; never drop a
  rule-bearing clause), then:
  - §6 `inv-policy-deny-list-single-source` and `inv-eeo-standing-consent`: "Next/Submit stay the
    user's" becomes "Submit stays the user's at every setting; Next is clicked only by
    `page_advance` during a queued run with full automation on";
  - `inv-job-site-password-local`: the Companion route, top-frame typing, never a model;
  - `inv-autofill-telemetry-no-values`: the known-gaps table (value-free, telemetry-gated, cleared
    by `DELETE /telemetry`) and the gesture report (event types and allowlisted key names only);
  - `inv-auto-consent-gated`: the `companion` channel and `companion-submitted`;
  - §7 Chrome extension + Guided fill: the queued run, per-tab memory, the Screening answers dial;
  - update pins in `.system_md_enforcement.json` for any new symbols named in §6.
- `extension/INTERNALS.md` ("never advances", "Mark applied is never automatic", session key notes,
  a new "Queued run" section), `extension/README.md:60`, `extension/shared/policy.js` header,
  `backend/app/schemas/eeo_consent.py:53-54` wording, CONTRIBUTING refusal #1 (the queued run is
  queued-only, capped, Submit stays human).
- `docs/plans/2026-10-05-full-automation-design.md`: one line under Owner decisions pointing to the
  queued-run design.
- `docs/entities/others.md`: the Form-filling section names the answer level; the auto-apply
  section names `prepare_queued`.
- A coverage table in `extension/README.md`: Workday / Greenhouse / Lever / Ashby / iCIMS → deep,
  generic or untested, from Task 14's fixtures.
- `CHANGELOG.md`: the queued run; the dial (and that installs with the old switch off now default to
  Backed by your history); per-tab memory; `apply_url`.

**Verify:** `python3 scripts/check_system_md.py` passes. Commit
`docs: the Companion's queued run — Submit stays yours, Next during a queued run`.

### Task 23: Full gate and the owner's live check

**Step 1:** From `backend/`: `pytest tests/ mcp_server/tests/ -q -n auto --dist loadfile`. Expected:
all pass. Report the count.

**Step 2:** Frontend: `cd frontend && npx tsc --noEmit && npm run lint`.

**Step 3:** Slop ratchet, EVERY surface touched, named in the claim:
`python3 ~/.claude/skills/ai-slop-detector/scripts/slop_scan.py check backend`, `... check frontend`,
`... check extension` (from the repo root). Re-baseline `complexity_hotspots` with a reason if only
that moved.

**Step 4:** Fresh stack per SYSTEM.md §9: uvicorn on a free port with its own SQLite file, plus
`API_PROXY_BACKEND=... npm run dev`. Check the Settings dial and the auto-apply switch in the
browser pane at 1280 and 1024 widths. Never verify against the docker-compose stack.

**Step 5: Owner live check** (the owner does this; write the checklist into the PR description):
load the unpacked extension; full automation on; queue five real jobs across Workday, Greenhouse,
Lever, Ashby and iCIMS; Apply to queued. For each: did it reach the real review page and stop?
Was Submit ever clicked (must be no)? What parked, and why? Seconds of your time per job (from the
run log). Then decide on Task 21.

---

## Spike result

(Task 1 appends the measured table and the chosen rule here.)
