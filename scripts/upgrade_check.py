#!/usr/bin/env python3
"""Prove `maestro update --source` moves a running install to a newer wheel and keeps the data.

    python3 scripts/upgrade_check.py --old OLD.whl --old-version 0.0.0.dev1
                                     --new NEW.whl --new-version 0.0.0.dev2 [--wait 120] [--keep]

Stdlib only; runs on Linux, macOS and Windows (the CI `uv-upgrade` job runs it on Ubuntu and
Windows). It installs OLD with `uv tool install`, starts it, creates a base resume through the
REST API, runs `maestro update --source NEW --yes`, then polls `maestro status --json` until the
backend reports the new version. Windows runs the upgrade in a detached batch file after the
command returns and must not be disturbed by launching maestro.exe meanwhile, so there it waits
for the batch file's own log (logs/update.log), then asks the server over HTTP, then runs `status`
once. It asserts: the backend version moved,
the base resume is still there, and a verified snapshot is in `backups/`. It finishes with
`maestro stop`.

Two Windows questions from the Task 6 review ride along on every OS, because they are cheap and
the smoke script does not cover them: `maestro start` straight after `maestro stop` must not say a
port is in use, and the facts print how long `stop` took and what the servers' logs say about their
shutdown (a `stop` over the supervisor's 10 s grace means a child needed the tree kill).

Everything lives in one temp folder (tool dir, shim dir, MAESTRO_HOME), deleted at the end unless
--keep. Ports come from MAESTRO_PORT / MAESTRO_WEB_PORT (default 8001 and 3000).
FASTEMBED_CACHE_PATH, when set, names a folder holding the scoring model; it is copied into the
install's home before start. Without it a marker file keeps `maestro start` from downloading the
model (this check never scores anything).
"""
from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

if sys.version_info < (3, 12):
    raise SystemExit("upgrade_check.py needs Python 3.12 or newer (this is %d.%d)." % sys.version_info[:2])

WINDOWS = sys.platform == "win32"
SLUG = "upgrade_check"
BACKUP_GLOB = "db-*.sqlite3.gz"  # maestro_cs/maintenance.py BACKUP_GLOB
LOG_TAIL = 40
# The same scrub list as scripts/smoke_uv_install.py: nothing of the developer's must leak in.
_SCRUBBED = (
    "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL", "FAST_MODEL", "SMART_MODEL", "CHAT_MODEL",
    "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "SYNC_REMOTE_URL", "SYNC_KEY_FILE",
    "SYNC_PUBLIC_HOST", "DATABASE_URL", "TEST_DATABASE_URL", "ALLOWED_HOSTS", "ALLOWED_WEB_ORIGINS",
    "FRONTEND_ALLOWED_HOSTS", "INTERNAL_API_URL", "BACKEND_URL", "MAESTRO_WEB_CMD", "MAESTRO_NODE",
    "MAESTRO_WEB_DIR", "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME",
)
RESUME = {
    "slug": SLUG,
    "display_name": "Upgrade check",
    "data": {
        "contact": {"name": "Upgrade Check", "email": "upgrade@example.invalid"},
        "summary": "Created before the update; it must still be here after.",
        "skills": [{"category": "Core", "items": ["Python"]}],
        "experience": [], "projects": [], "education": [], "certifications": [],
    },
}


class Failure(Exception):
    """A check failed; the message says what was expected and what was seen."""


# --- pure helpers (unit-tested in backend/tests/test_workflows.py) --------------------------

def parse_status(text: str) -> dict | None:
    """The `maestro status --json` report, or None when the text is not one (a launcher that is
    mid-replacement on Windows prints nothing usable)."""
    try:
        report = json.loads(text)
    except ValueError:
        return None
    return report if isinstance(report, dict) else None


def reports_version(report: dict | None, version: str) -> bool:
    backend = (report or {}).get("backend")
    return bool(report and report.get("running") is True and isinstance(backend, dict)
                and backend.get("version") == version)


def snapshots(home: Path) -> list[str]:
    return sorted(p.name for p in (home / "backups").glob(BACKUP_GLOB))


UPDATE_FINISHED = "[update] finished"  # the last line of the Windows update.cmd (update.py windows_script)
UPDATE_FAILED = "[update] The upgrade failed"  # uv failed; the script still restarts, then finishes


def update_log_state(text: str) -> str:
    """"failed", "finished" or "running" for the Windows update script's log. A failure line wins:
    the script restarts the old version and still prints the finished line."""
    if UPDATE_FAILED in text:
        return "failed"
    return "finished" if UPDATE_FINISHED in text else "running"


def wait_for_update_log(path: Path, timeout: float, *, sleep=time.sleep, clock=time.monotonic) -> str:
    """Wait for logs/update.log to say the Windows update script is done, reading only the file:
    launching maestro.exe or the venv python meanwhile would lock the files uv is replacing.
    Returns the log text; raises Failure on the failure line (at once) or on timeout."""
    deadline = clock() + timeout
    text = ""
    while True:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        state = update_log_state(text)
        if state == "failed":
            raise Failure("the update script reported a failed upgrade:\n" + "\n".join(text.splitlines()[-LOG_TAIL:]))
        if state == "finished":
            return text
        if clock() >= deadline:
            raise Failure(f"{path.name} did not reach '{UPDATE_FINISHED}' within {timeout:g} s; "
                          "last lines:\n" + "\n".join(text.splitlines()[-LOG_TAIL:]))
        sleep(2.0)


def tail_lines(path: Path, count: int = LOG_TAIL) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]
    except OSError:
        return []


def http_json(port: int, method: str, path: str, payload: object | None = None,
              timeout: float = 30.0) -> tuple[int, object]:
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
    finally:
        conn.close()
    try:
        return response.status, json.loads(data) if data else None
    except ValueError:
        return response.status, data[:200]


def port_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def _clear_readonly(func, target, _exc) -> None:
    with contextlib.suppress(OSError):
        os.chmod(target, stat.S_IWRITE)
        func(target)


class Check:
    def __init__(self, args: argparse.Namespace) -> None:
        self.old, self.new = args.old.resolve(), args.new.resolve()
        self.old_version, self.new_version = args.old_version, args.new_version
        self.wait, self.keep = args.wait, args.keep
        self.root = Path(tempfile.mkdtemp(prefix="maestro-upgrade-"))
        self.tool_dir, self.bin_dir, self.home = self.root / "tools", self.root / "bin", self.root / "home"
        self.shim = self.bin_dir / ("maestro.exe" if WINDOWS else "maestro")
        self.backend_port = int(os.environ.get("MAESTRO_PORT") or 8001)
        self.web_port = int(os.environ.get("MAESTRO_WEB_PORT") or 3000)
        self.model_seed = os.environ.get("FASTEMBED_CACHE_PATH", "").strip()
        self.env = self._build_env()
        self.facts: dict[str, object] = {}
        self.timings: list[tuple[str, float]] = []
        self.current = "setup"

    def _build_env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k.upper() not in _SCRUBBED}
        env.update(
            UV_TOOL_DIR=str(self.tool_dir), UV_TOOL_BIN_DIR=str(self.bin_dir),
            UV_NO_PROGRESS="1", MAESTRO_HOME=str(self.home),
            MAESTRO_PORT=str(self.backend_port), MAESTRO_WEB_PORT=str(self.web_port),
            NEXT_TELEMETRY_DISABLED="1", PYTHONUTF8="1",
            NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
        )
        env.pop("FASTEMBED_CACHE_PATH", None)  # the install sets its own; the seed is copied in
        return env

    # --- plumbing ---

    def say(self, message: str) -> None:
        print(message, flush=True)

    @contextlib.contextmanager
    def step(self, name: str) -> Iterator[None]:
        self.current = name
        started = time.monotonic()
        try:
            yield
        finally:
            self.timings.append((name, time.monotonic() - started))
        self.say(f"ok    {name} ({self.timings[-1][1]:.1f}s)")

    def run(self, argv: list[object], *, timeout: float = 120.0,
            expect_code: int | None = 0) -> subprocess.CompletedProcess:
        done = subprocess.run(
            [str(a) for a in argv], env=self.env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=timeout, check=False, encoding="utf-8", errors="replace",
        )
        if expect_code is not None and done.returncode != expect_code:
            out = "\n".join((done.stdout + done.stderr).strip().splitlines()[-LOG_TAIL:])
            raise Failure(f"`{Path(str(argv[0])).name} {' '.join(map(str, argv[1:]))}` exited "
                          f"{done.returncode}, expected {expect_code}:\n{out}")
        return done

    def maestro(self, *args: str, **kw) -> subprocess.CompletedProcess:
        return self.run([self.shim, *args], **kw)

    def expect_ok(self, condition: bool, message: str) -> None:
        if not condition:
            raise Failure(message)

    def backend_version(self) -> object:
        status, body = http_json(self.backend_port, "GET", "/api/version")
        self.expect_ok(status == 200 and isinstance(body, dict), f"GET /api/version returned {status}: {body!r}")
        return body.get("version")

    # --- the steps ---

    def install(self) -> None:
        uv = shutil.which("uv")
        self.expect_ok(bool(uv), "uv is not on PATH; install it from https://docs.astral.sh/uv/")
        self.run([uv, "tool", "install", "--python", "3.12", self.old], timeout=900)
        self.expect_ok(self.shim.is_file(), f"uv tool install did not create {self.shim}")
        text = self.maestro("--version").stdout.strip()
        self.expect_ok(text.endswith(self.old_version), f"`maestro --version` printed {text!r}, "
                       f"expected a line ending in {self.old_version}")

    def start(self) -> None:
        busy = [p for p in (self.backend_port, self.web_port) if port_listening(p)]
        self.expect_ok(not busy, f"port {busy[0] if busy else ''} already answers; pick spare ports with "
                       "MAESTRO_PORT and MAESTRO_WEB_PORT so this check never touches a running Maestro.")
        cache = self.home / "fastembed_cache"
        seed = Path(self.model_seed) if self.model_seed else None
        cache.mkdir(parents=True, exist_ok=True)
        if seed and seed.is_dir() and any(seed.iterdir()):
            shutil.copytree(seed, cache, dirs_exist_ok=True)
            self.facts["scoring model"] = f"seeded from {seed}"
        else:  # `maestro start` downloads the model only into an empty folder
            (cache / ".upgrade-check-no-model").write_text("this check never scores\n", encoding="utf-8")
            self.facts["scoring model"] = "not downloaded (marker file)"
        self.maestro("start", "--no-browser", timeout=900)
        got = self.backend_version()
        self.expect_ok(got == self.old_version, f"backend /api/version is {got!r}, expected {self.old_version}")

    def stop_then_start(self) -> None:
        """`maestro start` right after `maestro stop` must not report a port in use."""
        started = time.monotonic()
        self.maestro("stop", timeout=90)
        self.facts["stop took (s)"] = round(time.monotonic() - started, 1)
        for port in (self.backend_port, self.web_port):
            self.expect_ok(not port_listening(port), f"port {port} still answers after `maestro stop`")
        self.facts["shutdown (backend.log)"] = self._shutdown_facts()
        done = self.maestro("start", "--no-browser", timeout=300, expect_code=None)
        text = done.stdout + done.stderr
        self.expect_ok(done.returncode == 0 and "in use" not in text,
                       f"`maestro start` right after `stop` exited {done.returncode}:\n{text.strip()[-600:]}")
        self.maestro("restart", timeout=300)
        got = self.backend_version()
        self.expect_ok(got == self.old_version, f"after restart the backend reports {got!r}")

    def _shutdown_facts(self) -> str:
        """What uvicorn's log says about the stop (graceful = it ran its shutdown handlers)."""
        lines = tail_lines(self.home / "logs" / "backend.log", 60)
        graceful = [m for m in ("Shutting down", "Application shutdown complete", "Finished server process")
                    if any(m in line for line in lines)]
        return ", ".join(graceful) if graceful else "no uvicorn shutdown lines (killed, or not logged)"

    def create_resume(self) -> None:
        status, body = http_json(self.backend_port, "POST", "/api/base-resumes", RESUME, timeout=120.0)
        self.expect_ok(status == 200, f"POST /api/base-resumes returned {status}: {body!r}")
        self.expect_ok(isinstance(body, dict) and body.get("slug") == SLUG, f"unexpected create reply: {body!r}")
        self.facts["snapshots before update"] = len(snapshots(self.home))

    def update(self) -> None:
        done = self.maestro("update", "--source", self.new, "--yes", timeout=900)
        for line in (done.stdout + done.stderr).strip().splitlines()[-8:]:
            self.say(f"      | {line}")
        if WINDOWS:
            self.facts["update"] = "detached batch file; waited on logs/update.log, then HTTP, then one status"

    def wait_for_new(self) -> None:
        started = time.monotonic()
        if WINDOWS:
            # update.cmd runs `uv tool install --force` while we wait; any maestro.exe or venv
            # python we launched now would hold the files uv replaces. So: read the log only, then
            # ask the server over HTTP, and run the shim once everything has settled.
            wait_for_update_log(self.home / "logs" / "update.log", self.wait)
            self.wait_for_http_version()
            done = self.maestro("status", "--json", "--offline", timeout=60, expect_code=None)
            report = parse_status(done.stdout)
            self.expect_ok(reports_version(report, self.new_version),
                           f"`maestro status --json` does not report backend {self.new_version}: {report!r}")
        else:
            self.poll_status()
        self.facts["update took (s)"] = round(time.monotonic() - started, 1)

    def wait_for_http_version(self) -> None:
        deadline = time.monotonic() + self.wait
        seen: object = None
        while time.monotonic() < deadline:
            try:
                status, body = http_json(self.backend_port, "GET", "/api/version", timeout=5.0)
                seen = body.get("version") if status == 200 and isinstance(body, dict) else f"HTTP {status}"
            except OSError:
                seen = "no answer"
            if seen == self.new_version:
                return
            time.sleep(2.0)
        raise Failure(f"GET /api/version did not report {self.new_version} within {self.wait:g} s; last: {seen!r}")

    def poll_status(self) -> None:
        deadline = time.monotonic() + self.wait
        report = None
        while time.monotonic() < deadline:
            try:
                done = self.maestro("status", "--json", "--offline", timeout=60, expect_code=None)
                report = parse_status(done.stdout)
            except (OSError, subprocess.TimeoutExpired):
                report = None
            if reports_version(report, self.new_version):
                return
            time.sleep(2.0)
        raise Failure(f"`maestro status --json` did not report backend {self.new_version} within "
                      f"{self.wait:g} s; last report: {report!r}")

    def verify(self) -> None:
        got = self.backend_version()
        self.expect_ok(got == self.new_version, f"backend /api/version is {got!r}, expected {self.new_version}")
        text = self.maestro("--version").stdout.strip()
        self.expect_ok(text.endswith(self.new_version), f"`maestro --version` printed {text!r}, "
                       f"expected a line ending in {self.new_version}")
        status, body = http_json(self.backend_port, "GET", f"/api/base-resumes/{SLUG}")
        self.expect_ok(status == 200 and isinstance(body, dict) and body.get("slug") == SLUG,
                       f"the base resume {SLUG!r} is gone after the update: HTTP {status} {body!r}")
        after = snapshots(self.home)
        self.expect_ok(len(after) > self.facts["snapshots before update"],
                       f"no new snapshot in backups/ after the update: {after}")
        self.facts["snapshots"] = after
        # The web bundle is reused from the old wheel (build_wheel.py --web-from). The web
        # server's /api/version proxies to the backend, so it follows the backend; recorded only.
        status, web = http_json(self.web_port, "GET", "/api/version")
        self.facts["web version"] = web.get("version") if status == 200 and isinstance(web, dict) else f"HTTP {status}"

    def stop(self) -> None:
        self.maestro("stop", timeout=90)
        for port in (self.backend_port, self.web_port):
            self.expect_ok(not port_listening(port), f"port {port} still answers after `maestro stop`")
        done = self.maestro("status", "--json", "--offline", expect_code=None)
        self.expect_ok(done.returncode == 3, f"`maestro status` exited {done.returncode} after stop, expected 3")

    # --- the run ---

    def stop_quietly(self) -> None:
        if self.shim.is_file():
            with contextlib.suppress(Exception):
                self.maestro("stop", timeout=90, expect_code=None)
        for name in ("web", "backend", "supervisor"):  # last resort if `stop` could not
            record = self.home / "run" / f"{name}.json"
            try:
                pid = int(json.loads(record.read_text(encoding="utf-8"))["pid"])
            except (OSError, ValueError, KeyError):
                continue
            if any(port_listening(p) for p in (self.backend_port, self.web_port)):
                with contextlib.suppress(OSError):
                    if WINDOWS:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                       capture_output=True, check=False)
                    else:
                        os.kill(pid, 9)

    def dump_logs(self) -> None:
        logs = self.home / "logs"
        for name in ("update", "supervisor", "backend", "web"):
            lines = tail_lines(logs / f"{name}.log")
            if lines:
                print(f"--- last {len(lines)} lines of logs/{name}.log ---", file=sys.stderr)
                print("\n".join(lines), file=sys.stderr)

    def summary(self, total: float) -> None:
        print("\n=== upgrade summary ===")
        print(f"old -> new       {self.old.name} -> {self.new.name}")
        for key, value in self.facts.items():
            print(f"{key:<24} {value}")
        for name, seconds in self.timings:
            print(f"  {seconds:7.1f}s  {name}")
        print(f"total            {total:.1f}s")

    def main(self) -> int:
        started = time.monotonic()
        steps = (
            (f"uv tool install {self.old_version}", self.install),
            ("maestro start", self.start),
            ("start right after stop, then restart", self.stop_then_start),
            ("base resume created through the API", self.create_resume),
            (f"maestro update --source {self.new_version} --yes", self.update),
            (f"status reports backend {self.new_version}", self.wait_for_new),
            ("version moved, data kept, snapshot present", self.verify),
            ("maestro stop, ports free, status exits 3", self.stop),
        )
        code = 0
        try:
            for name, func in steps:
                with self.step(name):
                    func()
        except BaseException as exc:  # noqa: BLE001  (KeyboardInterrupt too: cleanup must run)
            code = 1
            message = str(exc) if isinstance(exc, Failure) else f"{type(exc).__name__}: {exc}"
            print(f"\nFAIL  {self.current}: {message}", file=sys.stderr)
            self.dump_logs()
        finally:
            self.stop_quietly()
            if self.keep:
                print(f"kept {self.root}")
            else:
                shutil.rmtree(self.root, onexc=_clear_readonly)
        if code == 0:
            self.summary(time.monotonic() - started)
        return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--old", required=True, type=Path, help="the wheel to install first")
    parser.add_argument("--old-version", required=True)
    parser.add_argument("--new", required=True, type=Path, help="the wheel `maestro update --source` installs")
    parser.add_argument("--new-version", required=True)
    parser.add_argument("--wait", type=float, default=120.0,
                        help="seconds to wait for the new version to answer after the update (default 120)")
    parser.add_argument("--keep", action="store_true", help="keep the temp tool dir and home")
    args = parser.parse_args(argv)
    for wheel in (args.old, args.new):
        if not wheel.is_file():
            print(f"upgrade_check: {wheel} is not a file", file=sys.stderr)
            return 1
    return Check(args).main()


if __name__ == "__main__":
    raise SystemExit(main())
