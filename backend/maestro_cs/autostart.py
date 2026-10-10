"""`maestro autostart on|off|status`: start Maestro at login with the system's own scheduler.

macOS uses a launchd agent, Linux a systemd user unit, Windows a Task Scheduler logon task. The
three `render_*` functions are pure; `Autostart` does the writing and calls the system tools
through an injected `runner`, so tests never touch launchctl, systemctl, loginctl or schtasks.

The platforms differ on "now": a macOS agent with `RunAtLoad` starts the moment it is bootstrapped
(harmless when Maestro already runs: `maestro run` exits 0 beside a live supervisor, and
`KeepAlive.SuccessfulExit = false` restarts only after a crash); a Linux unit is enabled without
`--now` and a Windows logon task waits for the next logon. The messages say which.

Windows runs `start --no-browser`, not `run`: `start` detaches the supervisor under a hidden
console `python.exe` (so stops stay graceful and no window appears), where `run` under the
windowless `pythonw.exe` would have no console to deliver CTRL_BREAK to.
"""
from __future__ import annotations

import getpass
import os
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from maestro_cs import runtime
from maestro_cs.control import ControlError
from maestro_cs.home import resolve_home

LABEL = "io.github.seinun-ai.maestro-career-studio"
UNIT_NAME = "maestro-career-studio.service"
TASK_NAME = "Maestro Career Studio"
ACTIONS = ("on", "off", "status")
CALL_TIMEOUT = 30.0
SCHTASKS_TR_LIMIT = 261  # Task Scheduler rejects a longer /TR value

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]

ON = "Autostart is on. Maestro will start when you log in; it is {now}."
ON_MAC = ON.format(now="starting now")
ON_LATER = ON.format(now="not started now")
OFF = "Autostart is off. Maestro no longer starts at login."
OFF_MAC_STOPPED = (
    "Autostart is off. Maestro no longer starts at login and has stopped; "
    "run `maestro start` to use it now."
)
NO_BOOT_LINGER = "Maestro will start when you log in, not at boot."
WINDOWS_HOME_NOTE = (
    "The login task can't carry MAESTRO_HOME from this terminal; it uses the default folder "
    "unless you set it for your account (setx MAESTRO_HOME \"<folder>\")."
)


# --- rendering (pure) ----------------------------------------------------------------------

def render_plist(shim: str | Path, home: Path, custom_home: bool) -> bytes:
    log = str(Path(home) / "logs" / "launchd.log")
    data: dict = {
        "Label": LABEL,
        "ProgramArguments": [str(shim), "run"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }
    if custom_home:
        data["EnvironmentVariables"] = {"MAESTRO_HOME": str(home)}
    return plistlib.dumps(data)


def _unit_word(text: str) -> str:
    """One systemd word: `%` and `$` are always special; quote when spaces or quotes appear."""
    text = text.replace("%", "%%").replace("$", "$$")
    if any(c.isspace() or c in "\"'\\" for c in text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def render_unit(shim: str | Path, home: Path, custom_home: bool) -> str:
    lines = [
        "[Unit]",
        "Description=Maestro Career Studio",
        "",
        "[Service]",
        f"ExecStart={_unit_word(str(shim))} run",
        "Restart=on-failure",
        "RestartSec=5",
    ]
    if custom_home:
        lines.append(f"Environment={_unit_word('MAESTRO_HOME=' + str(home))}")
    lines += ["", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def render_schtasks_create(python: str | Path) -> list[str]:
    program = str(python)
    if any(c.isspace() for c in program):
        program = f'"{program}"'
    command = f"{program} -m maestro_cs start --no-browser"
    if len(command) > SCHTASKS_TR_LIMIT:
        raise ControlError(
            "The login task's command is too long for Windows Task Scheduler "
            f"({len(command)} of {SCHTASKS_TR_LIMIT} characters). Install Maestro under a shorter path."
        )
    return ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "ONLOGON", "/RL", "LIMITED", "/F",
            "/TR", command]


# --- system calls --------------------------------------------------------------------------

def default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), capture_output=True, text=True, errors="replace",
        stdin=subprocess.DEVNULL, timeout=CALL_TIMEOUT, check=False,
    )


def _detail(done: subprocess.CompletedProcess[str]) -> str:
    text = f"{done.stderr or ''}\n{done.stdout or ''}"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[0] if lines else f"exit code {done.returncode}"


def _write(path: Path, data: bytes, mode: int) -> None:
    """Write through a symlink (dotfile managers) with a same-folder temp file and a rename."""
    target = Path(os.path.realpath(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    try:
        temp.write_bytes(data)
        temp.chmod(mode)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _restore(path: Path, prior: bytes | None, mode: int) -> None:
    try:
        if prior is None:
            path.unlink(missing_ok=True)
        else:
            _write(path, prior, mode)
    except OSError:
        pass


class Autostart:
    def __init__(
        self,
        home: Path,
        environ: Mapping[str, str],
        *,
        platform: str = sys.platform,
        user_home: Path | None = None,
        runner: Runner = default_runner,
        say: Callable[[str], None] = print,
        shim: str | Path | None = None,
        python: str | Path | None = None,
        which: Callable[[str], str | None] = shutil.which,
        uid: int | None = None,
        user: str | None = None,
    ) -> None:
        self.home, self.environ, self.platform = Path(home), environ, platform
        self.user_home = Path(user_home) if user_home else Path.home()
        self.runner, self.say, self.which = runner, say, which
        self._shim, self._python, self._uid, self._user = shim, python, uid, user

    # --- where things live ------------------------------------------------------------

    @property
    def kind(self) -> str:
        if self.platform == "darwin":
            return "mac"
        if self.platform == "win32":
            return "windows"
        if self.platform.startswith("linux"):
            return "linux"
        raise ControlError(f"Autostart isn't supported on {self.platform}.")

    @property
    def custom_home(self) -> bool:
        return bool((self.environ.get("MAESTRO_HOME") or "").strip())

    @property
    def plist_path(self) -> Path:
        return self.user_home / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    @property
    def unit_dir(self) -> Path:
        config = (self.environ.get("XDG_CONFIG_HOME") or "").strip()
        base = Path(config) if config and os.path.isabs(config) else self.user_home / ".config"
        return base / "systemd" / "user"

    @property
    def unit_path(self) -> Path:
        return self.unit_dir / UNIT_NAME

    @property
    def shim(self) -> str:
        return str(self._shim if self._shim is not None else runtime.shim_path())

    @property
    def python(self) -> str:
        if self._python is not None:
            return str(self._python)
        return str(runtime.venv_python(windowless=True))

    @property
    def uid(self) -> int:
        return self._uid if self._uid is not None else os.getuid()

    @property
    def user(self) -> str:
        return self._user or getpass.getuser()

    # --- one system call --------------------------------------------------------------

    def _call(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
        """The finished call, or None when the program does not exist."""
        try:
            return self.runner(argv)
        except FileNotFoundError:
            return None
        except subprocess.TimeoutExpired:
            raise ControlError(f"{argv[0]} did not answer in {CALL_TIMEOUT:g} seconds.") from None
        except OSError as exc:
            raise ControlError(f"Cannot run {argv[0]}: {exc.strerror or exc}.") from None

    def _must(self, argv: Sequence[str], what: str) -> None:
        done = self._call(argv)
        if done is None:
            raise ControlError(f"{what}: {argv[0]} is not available.")
        if done.returncode != 0:
            raise ControlError(f"{what}: {_detail(done)}")

    # --- status -----------------------------------------------------------------------

    def is_enabled(self) -> bool | None:
        kind = self.kind
        if kind == "mac":
            return os.path.lexists(self.plist_path)
        if kind == "linux":
            return self.unit_path.exists() and os.path.lexists(
                self.unit_dir / "default.target.wants" / UNIT_NAME
            )
        done = self._call(["schtasks", "/Query", "/TN", TASK_NAME])
        return None if done is None else done.returncode == 0

    # --- on / off ---------------------------------------------------------------------

    def handle(self, action: str) -> int:
        if action == "status":
            enabled = self.is_enabled()
            self.say("Autostart: can't tell on this computer." if enabled is None
                     else f"Autostart is {'on' if enabled else 'off'}.")
            return 0
        kind = self.kind
        if action == "on":
            getattr(self, f"_on_{kind}")()
        else:
            if not self.is_enabled():
                self.say("Autostart is already off.")
                return 0
            getattr(self, f"_off_{kind}")()
        return 0

    # macOS

    def _on_mac(self) -> None:
        path, mode = self.plist_path, 0o644
        wanted = render_plist(self.shim, self.home, self.custom_home)
        prior = path.read_bytes() if path.is_file() else None
        if prior == wanted:
            self.say("Autostart is already on.")
            return
        if prior is not None:  # a loaded job points at the old program; replace it
            self._call(["launchctl", "bootout", f"gui/{self.uid}/{LABEL}"])
        try:
            (self.home / "logs").mkdir(parents=True, exist_ok=True)  # launchd will not
            _write(path, wanted, mode)
        except OSError as exc:
            raise ControlError(f"Cannot write {path}: {exc.strerror or exc}.") from None
        try:
            self._must(["launchctl", "bootstrap", f"gui/{self.uid}", str(path)],
                       "launchd would not start Maestro")
        except ControlError:
            _restore(path, prior, mode)
            raise
        self.say(ON_MAC)

    def _off_mac(self) -> None:
        done = self._call(["launchctl", "bootout", f"gui/{self.uid}/{LABEL}"])
        try:
            self.plist_path.unlink(missing_ok=True)
        except OSError as exc:
            raise ControlError(f"Cannot delete {self.plist_path}: {exc.strerror or exc}.") from None
        self.say(OFF_MAC_STOPPED if done is not None and done.returncode == 0 else OFF)

    # Linux

    def _on_linux(self) -> None:
        if not self.which("systemctl"):
            raise ControlError("Autostart needs systemd, which this computer doesn't seem to have.")
        path, mode = self.unit_path, 0o644
        prior = path.read_bytes() if path.is_file() else None
        try:
            _write(path, render_unit(self.shim, self.home, self.custom_home).encode(), mode)
        except OSError as exc:
            raise ControlError(f"Cannot write {path}: {exc.strerror or exc}.") from None
        try:
            self._must(["systemctl", "--user", "daemon-reload"], "systemd would not reload")
            self._must(["systemctl", "--user", "enable", UNIT_NAME], "systemd would not enable Maestro")
        except ControlError:
            _restore(path, prior, mode)
            raise
        self.say(ON_LATER)
        done = self._call(["loginctl", "enable-linger", self.user])
        if done is None or done.returncode != 0:
            self.say(NO_BOOT_LINGER)

    def _off_linux(self) -> None:
        self._call(["systemctl", "--user", "disable", UNIT_NAME])
        try:
            self.unit_path.unlink(missing_ok=True)
        except OSError as exc:
            raise ControlError(f"Cannot delete {self.unit_path}: {exc.strerror or exc}.") from None
        self._call(["systemctl", "--user", "daemon-reload"])
        self.say(OFF)

    # Windows

    def _on_windows(self) -> None:
        self._must(render_schtasks_create(self.python), "Windows would not create the login task")
        self.say(ON_LATER)
        if self.custom_home:
            self.say(WINDOWS_HOME_NOTE)

    def _off_windows(self) -> None:
        self._must(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], "Windows would not remove the login task")
        self.say(OFF)


def is_enabled(platform: str = sys.platform, environ: Mapping[str, str] = os.environ,
               user_home: Path | None = None, runner: Runner = default_runner) -> bool | None:
    """For `maestro status`: True/False, or None when the system can't be asked."""
    try:
        return Autostart(resolve_home(environ), environ, platform=platform,
                         user_home=user_home, runner=runner).is_enabled()
    except ControlError:
        return None
