"""`maestro update`: snapshot, stop, upgrade with uv, start, verify.

Order is the safety property: the version check, the changelog and the confirmation change
nothing; a verified snapshot comes before anything is stopped; uv comes last. A failed snapshot
aborts with nothing stopped or changed.

Every network call is named on screen: pypi.org (is there a newer version) and
raw.githubusercontent.com (the changelog's breaking changes). Both are plain GETs and a failure
costs one sentence, never the update itself (the changelog) or a traceback (the check).

POSIX runs uv in this process. uv replaces the venv's files under us, so everything the code
after it can need is imported first (`_preload`) and the new version is started as a *new*
process. Windows cannot replace a running `maestro.exe` or the venv's `python.exe`, so the
upgrade is handed to a detached `cmd` script that waits for this process to exit (R6).
"""
from __future__ import annotations

import contextlib
import ntpath
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import psutil

from maestro_cs import control, detach, home as home_mod, maintenance, procs, runtime
from maestro_cs.control import Context, ControlError

PACKAGE = "maestro-career-studio"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
CHANGELOG_RAW = "https://raw.githubusercontent.com/seinun-ai/maestro-career-studio/v{version}/CHANGELOG.md"
CHANGELOG_PAGE = "https://github.com/seinun-ai/maestro-career-studio/blob/v{version}/CHANGELOG.md"
NETWORK_TIMEOUT = 3.0
PYTHON = "3.12"
WAIT_SECONDS = 30
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
SECTION_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)
WHEEL_RE = re.compile(r"(?i)^maestro_career_studio-([^-]+)-")
UV_HELP = "https://docs.astral.sh/uv/getting-started/installation/"

FetchText = Callable[[str, float], str | None]


# --- versions -------------------------------------------------------------------------------

def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """`X.Y.Z` as a tuple; anything else (a pre-release, `dev`, junk) is None."""
    cleaned = (text or "").strip().removeprefix("v")
    if not VERSION_RE.fullmatch(cleaned):
        return None
    major, minor, patch = (int(part) for part in cleaned.split("."))
    return major, minor, patch


def is_newer(candidate: str | None, current: str | None) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return new is not None and old is not None and new > old


# --- network (injected in tests) --------------------------------------------------------------

def fetch_text(url: str, timeout: float = NETWORK_TIMEOUT) -> str | None:
    """GET a URL as text; None on any failure. Honors proxy variables (`trust_env`)."""
    try:
        response = httpx.get(url, timeout=timeout, trust_env=True, follow_redirects=True)
    except (httpx.HTTPError, OSError, ValueError):
        return None
    return response.text if response.status_code == 200 else None


def latest_version(timeout: float = NETWORK_TIMEOUT, fetch: FetchText | None = None) -> str | None:
    """The newest stable release on PyPI, or None when it couldn't be read."""
    body = (fetch or fetch_text)(PYPI_URL, timeout)  # looked up now, so a test's patch applies
    if body is None:
        return None
    import json  # noqa: PLC0415  (only used here)

    try:
        data = json.loads(body)
        info_version = str(data["info"]["version"])
        releases = data.get("releases") or {}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if parse_version(info_version):
        return info_version
    # info.version is a pre-release only when nothing stable exists; look for a stable one anyway.
    return max(_stable_versions(releases), key=lambda v: parse_version(v) or (0, 0, 0), default=None)


def _stable_versions(releases: Any) -> list[str]:
    """Release names that parse as `X.Y.Z` and have at least one file that isn't yanked."""
    if not isinstance(releases, dict):
        return []
    return [v for v, files in releases.items()
            if isinstance(v, str) and parse_version(v) and isinstance(files, list)
            and any(isinstance(f, dict) and not f.get("yanked") for f in files)]


def breaking_notes(current: str, new: str, fetch: FetchText | None = None,
                   timeout: float = NETWORK_TIMEOUT) -> list[tuple[str, str]] | None:
    """The `### Breaking changes` body of every release in (current, new], newest first.

    None when the changelog couldn't be read. A release without that heading, or with an empty
    one, is left out.
    """
    text = (fetch or fetch_text)(CHANGELOG_RAW.format(version=new), timeout)
    if text is None:
        return None
    return parse_breaking(text, current, new)


def parse_breaking(text: str, current: str, new: str) -> list[tuple[str, str]]:
    low, high = parse_version(current), parse_version(new)
    if high is None:
        return []
    found = list(SECTION_RE.finditer(text))
    notes: list[tuple[str, str]] = []
    for index, match in enumerate(found):
        version = parse_version(match.group(1))
        if version is None or not ((low is None or version > low) and version <= high):
            continue
        end = found[index + 1].start() if index + 1 < len(found) else len(text)
        body = _breaking_body(text[match.end():end])
        if body:
            notes.append((match.group(1), body))
    return notes


def _breaking_body(section: str) -> str:
    lines = section.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if re.match(r"^### Breaking changes\s*$", ln, re.IGNORECASE)), None)
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## ") or line.startswith("### "):
            break
        body.append(line)
    return "\n".join(body).strip()


# --- uv and the commands ----------------------------------------------------------------------

def find_uv(environ: Mapping[str, str], platform: str = sys.platform,
            which: Callable[..., str | None] = shutil.which,
            home: Path | None = None) -> Path | None:
    """`uv` on PATH, else where its installer puts it (a GUI-launched shell may lack PATH)."""
    found = which("uv")
    if found:
        return Path(found)
    if home is not None:
        user = home
    elif platform == "win32" and environ.get("USERPROFILE"):
        user = Path(environ["USERPROFILE"])
    else:
        user = Path.home()
    name = "uv.exe" if platform == "win32" else "uv"
    places = [user / ".local" / "bin" / name, user / ".cargo" / "bin" / name]
    return next((p for p in places if p.is_file()), None)


def upgrade_argv(uv: Path, source: str | None, new: str | None) -> list[str]:
    """Always `uv tool install --force`: it installs exactly the version whose changelog was shown
    and clears the `==<old>` pin a documented rollback leaves (`uv tool upgrade` honours it)."""
    return [str(uv), "tool", "install", "--force", "--python", PYTHON, source or f"{PACKAGE}=={new}"]


def manual_command(source: str | None, new: str | None) -> str:
    return f"uv tool install --force --python {PYTHON} {source or f'{PACKAGE}=={new}'}"


def rollback_command(old: str) -> str:
    return f"uv tool install --force --python {PYTHON} {PACKAGE}=={old}"


def wheel_version(source: str | None) -> str | None:
    match = WHEEL_RE.match(Path(source).name) if source else None
    return match.group(1) if match else None


def _cmd_text(text: str) -> str:
    """Make `text` safe on a batch `echo` line (escape cmd's special characters)."""
    return re.sub(r"([\^&|<>()])", r"^\1", text.replace("%", "%%"))


def _cmd_quote(value: str) -> str:
    return '"' + value.replace("%", "%%") + '"'


def windows_script(*, wait_pids: list[int], uv_argv: list[str], shim: Path, restart: bool,
                   snapshot: str | None, old: str, wait_seconds: int = WAIT_SECONDS) -> str:
    """The batch file the Windows handoff runs. Pure text; every path quoted (spaces, parens).

    It waits for the pids (this process and its launcher stub) to exit, runs uv, then starts
    Maestro again when it was running before. Nothing in it runs from the venv's python.
    """
    lines = ["@echo off", "chcp 65001 >nul", "setlocal",
             f"echo [update] waiting for maestro to exit ({', '.join(map(str, wait_pids))})"]
    for n, pid in enumerate(wait_pids):
        lines += [
            f"set /a tries{n}=0",
            f":wait{n}",
            f'tasklist /FI "PID eq {pid}" /NH /FO CSV 2>nul | find ",""{pid}""," >nul',
            f"if errorlevel 1 goto done{n}",
            f"set /a tries{n}+=1",
            f"if %tries{n}% GEQ {wait_seconds} goto done{n}",
            "ping -n 2 127.0.0.1 >nul",  # one second; `timeout` refuses redirected input
            f"goto wait{n}",
            f":done{n}",
        ]
    command = " ".join(_cmd_quote(part) for part in uv_argv)
    lines += [
        "echo [update] running uv",
        command,
        "if errorlevel 1 goto failed",
        "echo [update] upgraded",
        "goto restart",
        ":failed",
        "echo [update] The upgrade failed. Your data is untouched.",
    ]
    if snapshot:
        lines.append(f"echo [update] Snapshot saved first: {_cmd_text(snapshot)}")
    lines += [
        f"echo [update] To pin the old version: {_cmd_text(rollback_command(old))}",
        ":restart",
    ]
    if restart:
        lines += [
            "echo [update] starting Maestro",
            f"{_cmd_quote(str(shim))} start --no-browser",
            f"{_cmd_quote(str(shim))} status --offline",
        ]
    lines += ["echo [update] finished", "exit /b 0"]
    return "\r\n".join(lines) + "\r\n"


def windows_handoff_argv() -> list[str]:
    """Run the script by its bare name from the logs folder (cwd): no quoting for `cmd /c` to eat."""
    return ["cmd", "/d", "/c", ".\\update.cmd"]


# --- other processes from this venv ---------------------------------------------------------

Found = tuple[int, list[str]]


def _inside_prefix(prefix: str, platform: str,
                   resolve: Callable[[str], str]) -> Callable[[str], bool]:
    """A test for "this path is inside `prefix`" (case-insensitive on Windows)."""
    windows = platform == "win32"
    sep = "\\" if windows else "/"

    def norm(path: str) -> str:
        return ntpath.normcase(resolve(path)) if windows else resolve(path)

    root = norm(prefix).rstrip(sep) + sep
    return lambda path: (norm(path) + sep).startswith(root)


def find_venv_processes(prefix: str, exclude: set[int], processes: Iterable[Any],
                        platform: str = sys.platform,
                        resolve: Callable[[str], str] = os.path.realpath) -> list[Found]:
    """(pid, command line) of live processes whose executable is inside `prefix`.

    Windows paths compare case-insensitively. A process that vanishes or won't say (AccessDenied)
    is skipped, or listed without a command line, never an error.
    """
    inside = _inside_prefix(prefix, platform, resolve)
    found: list[Found] = []
    for proc in processes:
        if proc.pid in exclude:
            continue
        try:
            exe = proc.exe()
        except psutil.Error:
            continue
        if not exe or not inside(exe):
            continue
        try:
            cmdline = list(proc.cmdline())
        except psutil.Error:
            cmdline = []
        found.append((proc.pid, cmdline))
    return sorted(found, key=lambda item: item[0])


def blocked_message(found: list[Found]) -> str:
    """One sentence naming what keeps uv from replacing the venv's files."""
    ids = ", ".join(str(pid) for pid, args in found if "mcp" not in args)
    others = f" (process ids {ids})" if ids else ""
    if any("mcp" in args for _, args in found):
        return (f"Quit Claude Desktop (it runs `maestro mcp`) and stop other `maestro` commands"
                f"{others}, then run `maestro update`.")
    return (f"Stop the other `maestro` commands that are still running{others}, "
            "then run `maestro update`.")


# --- the flow ---------------------------------------------------------------------------------

def _preload() -> None:
    """Import what the post-upgrade code can need, before uv swaps the venv's files (R6)."""
    import encodings.idna  # noqa: F401,PLC0415  (socket imports it lazily on first hostname use)
    import http.client  # noqa: F401,PLC0415
    import json  # noqa: F401,PLC0415
    import subprocess  # noqa: F401,PLC0415
    import urllib.request  # noqa: F401,PLC0415
    import webbrowser  # noqa: F401,PLC0415

    from maestro_cs import autostart, procs  # noqa: F401,PLC0415


class Updater:
    def __init__(
        self,
        ctx: Context,
        *,
        fetch: FetchText | None = None,
        run: Callable[..., Any] = subprocess.run,
        popen: Callable[..., Any] = subprocess.Popen,
        ask: Callable[[str], str] = input,
        say: Callable[..., None] = print,
        make_maintenance: Callable[..., Any] = maintenance.Maintenance,
        make_controller: Callable[..., Any] = control.Controller,
        which: Callable[..., str | None] = shutil.which,
        http_get: Callable[..., dict[str, Any] | None] = control.http_get_json,
        platform: str = sys.platform,
        sleep: Callable[[float], None] = time.sleep,
        launcher_pids: Callable[[], list[int]] | None = None,
        preload: Callable[[], None] = _preload,
        user_home: Path | None = None,
        blockers: Callable[[], list[Found]] | None = None,
        process_iter: Callable[[], Iterable[Any]] = psutil.process_iter,
        prefix: str = sys.prefix,
        resolve: Callable[[str], str] = os.path.realpath,
    ) -> None:
        self.ctx, self.fetch, self.run_cmd, self.popen = ctx, fetch or fetch_text, run, popen
        self.ask, self.say = ask, say
        self.make_maintenance, self.make_controller, self.which = make_maintenance, make_controller, which
        self.http_get, self.platform, self.sleep = http_get, platform, sleep
        self.launcher_pids = launcher_pids or self._own_pids
        self.preload, self.user_home = preload, user_home
        self.blockers = blockers or self._venv_blockers
        self.process_iter, self.prefix, self.resolve = process_iter, prefix, resolve

    # --- steps --------------------------------------------------------------------------

    def _check(self, source: str | None) -> str | None | int:
        """The version to move to, or an exit code when there is nothing to do."""
        current = self.ctx.version
        if source:
            if not Path(source).expanduser().is_file():
                raise ControlError(f"There is no file at {source}.")
            return wheel_version(source) or ""
        if parse_version(current) is None:
            raise ControlError("This copy of Maestro isn't a released install, so there is "
                               "nothing to update.")
        self.say("Checking pypi.org for the newest version (one request)…")
        latest = latest_version(NETWORK_TIMEOUT, self.fetch)
        if latest is None:
            raise ControlError("Couldn't check for a newer version (pypi.org didn't answer). "
                               "Check your connection and run `maestro update` again.")
        if not is_newer(latest, current):
            self.say(f"Maestro {current} is the newest version.")
            return 0
        return latest

    def _show_notes(self, current: str, new: str) -> None:
        self.say("Reading the changelog from raw.githubusercontent.com (one request)…")
        notes = breaking_notes(current, new, self.fetch)
        if notes is None:
            self.say(f"Couldn't read the changelog; see {CHANGELOG_PAGE.format(version=new)}.")
            return
        if not notes:
            self.say(f"The changelog lists no breaking changes between {current} and {new}.")
            return
        self.say(f"Breaking changes between {current} and {new}:")
        for version, body in notes:
            self.say(f"\n  {version}")
            for line in body.splitlines():
                self.say(f"    {line}" if line.strip() else "")
        self.say("")

    def _confirm(self, question: str, yes: bool) -> bool:
        if yes:
            return True
        try:
            answer = self.ask(f"{question} [y/N] ")
        except EOFError:
            raise ControlError("There is no one to ask here; run `maestro update --yes` to "
                               "update without asking.") from None
        return answer.strip().lower() in ("y", "yes")

    def _snapshot(self) -> str | None:
        """A verified snapshot of the database; ControlError (nothing changed) when it fails."""
        lines: list[str] = []
        self.make_maintenance(self.ctx, say=lines.append).backup()
        if not lines or lines[-1].startswith("Nothing to back up"):
            self.say("No database yet, so nothing to snapshot.")
            return None
        self.say(f"Snapshot saved: {lines[-1]}")
        return lines[-1]

    def _log(self, text: str) -> None:
        with contextlib.suppress(OSError):  # the log is a courtesy; the screen already said it
            with open(self.ctx.logs_dir / "update.log", "a", encoding="utf-8") as log:
                log.write(f"[{datetime.now().isoformat(timespec='seconds')}] {text.rstrip()}\n")

    @staticmethod
    def _own_pids(get_process: Callable[[], Any] = psutil.Process) -> list[int]:
        """This process and up to two ancestors that are part of its launcher chain.

        Under uv on Windows the chain is `maestro.exe` (trampoline) -> the venv's `python.exe`
        -> the base `python.exe`; all three hold files uv is about to replace. An ancestor whose
        name isn't `python*`/`maestro*` (a shell, Explorer) ends the walk.
        """
        pids = [os.getpid()]
        with contextlib.suppress(psutil.Error):  # a vanished ancestor just ends the walk
            node = get_process()
            for _ in range(2):
                node = node.parent()
                if node is None or not node.name().lower().startswith(("python", "maestro")):
                    break
                pids.append(node.pid)
        return pids

    def _will_be_stopped(self) -> set[int]:
        """Pids `stop` ends (the supervisor, its servers and their children): not blockers.

        Also up to two launcher ancestors of each. On Windows `Scripts\\python.exe` in a uv venv is
        a launcher that starts the base interpreter as its child, so a record holds the child's pid
        and the launcher (an exe under the prefix) is its parent. It waits on that child and exits
        with it, so stopping the supervisor ends it too. The connect waiter is NOT here: `stop`
        leaves it running, and it holds files uv must replace, so it correctly blocks.
        """
        pids: set[int] = set()
        for name in ("supervisor", "web", "backend"):
            live = procs.live_process(procs.read_json(self.ctx.run_dir / f"{name}.json"))
            if live is None:
                continue
            pids.add(live.pid)
            with contextlib.suppress(psutil.Error):
                pids.update(child.pid for child in live.children(recursive=True))
            pids.update(self._launcher_ancestors(live))
        return pids

    def _launcher_ancestors(self, proc: Any) -> list[int]:
        """Up to two parents that are Python or inside this venv (mirrors `_own_pids`)."""
        inside = _inside_prefix(self.prefix, self.platform, self.resolve)
        found: list[int] = []
        with contextlib.suppress(psutil.Error):  # a vanished ancestor just ends the walk
            node = proc
            for _ in range(2):
                node = node.parent()
                if node is None:
                    break
                is_python = node.name().lower().startswith("python")
                if not (is_python or inside(node.exe())):
                    break
                found.append(node.pid)
        return found

    def _venv_blockers(self) -> list[Found]:
        """Other processes running from this venv: they lock files uv must replace (Windows)."""
        exclude = {*self.launcher_pids(), *self._will_be_stopped()}
        return find_venv_processes(self.prefix, exclude, self.process_iter(), self.platform,
                                   self.resolve)

    def _windows_handoff(self, uv_argv: list[str], shim: Path, restart: bool,
                         snapshot: str | None, old: str) -> int:
        logs = self.ctx.logs_dir
        script = windows_script(wait_pids=self.launcher_pids(), uv_argv=uv_argv, shim=shim,
                                restart=restart, snapshot=snapshot, old=old)
        try:
            (logs / "update.cmd").write_bytes(script.encode("utf-8"))
            with open(logs / "update.log", "ab") as log:
                # Built here, not via detach.spawn_detached: that runs the venv's python, which
                # uv is about to replace. `cmd.exe` is outside the venv.
                detach.popen_windows(
                    self.popen, windows_handoff_argv(),
                    detach.CREATE_NEW_PROCESS_GROUP | detach.CREATE_NO_WINDOW,
                    cwd=logs, env=home_mod.child_env(self.ctx.environ, platform=self.platform),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        except OSError as exc:
            raise ControlError(f"Couldn't hand the update to a background step: "
                               f"{exc.strerror or exc}. Maestro is stopped; run `maestro start`.") from None
        log_path = self.ctx.home / "logs" / "update.log"
        self.say(f"Updating in the background; it takes about a minute. Don't run `maestro` "
                 f"until {log_path} ends with \"finished\", then run `maestro status`.")
        return 0

    def _run_uv(self, argv: list[str]) -> tuple[int, str]:
        try:
            done = self.run_cmd(argv, cwd=self.ctx.home, env=home_mod.child_env(self.ctx.environ),
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                check=False)
        except OSError as exc:
            return 1, f"{argv[0]} could not be run ({exc.strerror or exc})."
        return done.returncode, "\n".join(filter(None, [done.stdout, done.stderr])).strip()

    def _start_new(self, shim: Path) -> int:
        """Start through the (possibly new) launcher, as a new process: this one has old code."""
        try:
            done = self.run_cmd([str(shim), "start", "--no-browser"], cwd=self.ctx.home,
                                env=home_mod.child_env(self.ctx.environ), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, check=False)
        except OSError as exc:
            self.say(f"Couldn't start Maestro: {exc.strerror or exc}.")
            return 1
        for line in filter(None, (done.stdout or "").splitlines() + (done.stderr or "").splitlines()):
            self.say(line)
        return done.returncode

    def _installed_version(self, shim: Path) -> str | None:
        try:
            done = self.run_cmd([str(shim), "--version"], cwd=self.ctx.home,
                                env=home_mod.child_env(self.ctx.environ), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, check=False)
        except OSError:
            return None
        parts = (done.stdout or "").split()
        return parts[-1] if done.returncode == 0 and parts else None

    def _running_version(self) -> str | None:
        for attempt in range(3):
            info = self.http_get(f"http://127.0.0.1:{self.ctx.ports.backend}/api/version")
            if info and isinstance(info.get("version"), str):
                return info["version"]
            if attempt < 2:
                self.sleep(1.0)
        return None

    def _mismatch(self, got: str | None, expected: str, snapshot: str | None, old: str) -> int:
        self.say(f"Maestro is on version {got or 'unknown'}, not the {expected} that was expected.")
        return self._recovery(snapshot, old)

    def _recovery(self, snapshot: str | None, old: str) -> int:
        if snapshot:
            self.say(f"Your data was saved before the update: {snapshot}")
        self.say(f"To go back to {old}: {rollback_command(old)}")
        self.say("Migrations only run forward, so a downgrade may not open data the new version "
                 "changed; if it doesn't, restore the snapshot above.")
        return 1

    # --- the verb -----------------------------------------------------------------------

    def run(self, source: str | None = None, yes: bool = False) -> int:
        ctx = self.ctx
        old = ctx.version
        if source:
            # uv runs with cwd = the data folder, not the caller's; hand it an absolute path.
            source = str(Path(source).expanduser().resolve())
        checked = self._check(source)
        if isinstance(checked, int):
            return checked
        new = checked or None  # a wheel with an unrecognised name: version unknown
        if self.platform == "win32":  # POSIX replaces open files in place; Windows cannot
            blocking = self.blockers()
            if blocking:
                raise ControlError(blocked_message(blocking))
        if not source and new:
            self._show_notes(old, new)
        target = new or source or "the new version"
        if not self._confirm(f"Update from {old} to {target}?", yes):
            self.say("Nothing changed.")
            return 0
        uv = find_uv(ctx.environ, self.platform, self.which, self.user_home)
        if uv is None:
            self.say(f"Maestro can't update itself: `uv` wasn't found. Run `{manual_command(source, new)}` "
                     f"in a terminal (install uv first: {UV_HELP}). Nothing was changed.")
            return 1
        snapshot = self._snapshot()  # raises ControlError "The backup failed: …": nothing stopped
        shim = runtime.shim_path(self.platform)
        was_running = control.running_supervisor(ctx) is not None
        if was_running:
            self.make_controller(ctx).stop()
        argv = upgrade_argv(uv, source, new)
        if self.platform == "win32":
            return self._windows_handoff(argv, shim, was_running, snapshot, old)
        self.preload()
        self.say("Updating…")
        code, output = self._run_uv(argv)
        self._log(f"{' '.join(argv)} -> {code}\n{output}")
        if code != 0:
            self.say(output or "uv failed without saying why.")
            self.say("The update failed; your data is untouched.")
            if was_running:
                self.say("Starting the previous version again…")
                self._start_new(shim)
            return 1
        if was_running and self._start_new(shim) != 0:
            self.say("Maestro didn't start after the update.")
            return self._recovery(snapshot, old)
        if new is None:
            self.say("Updated." + ("" if was_running else " Start it with `maestro start`."))
            return 0
        got = self._running_version() if was_running else self._installed_version(shim)
        if got != new:
            return self._mismatch(got, new, snapshot, old)
        self.say(f"Maestro is now version {new}." + ("" if was_running else " Start it with `maestro start`."))
        return 0
