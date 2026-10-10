"""`maestro autostart on|off|status`: rendering is pure, every system call goes through a runner.

Nothing here runs launchctl, systemctl, loginctl or schtasks, and nothing writes outside tmp_path.
"""
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

from maestro_cs import autostart, cli, control
from maestro_cs.autostart import (
    LABEL,
    TASK_NAME,
    UNIT_NAME,
    Autostart,
    render_plist,
    render_schtasks_create,
    render_unit,
)
from maestro_cs.control import ControlError

SHIM = "/Users/ann/.local/share/uv/tools/maestro-career-studio/bin/maestro"
SHIM_SPACES = "/Users/ann lee/.local/share/uv/tools/maestro-career-studio/bin/maestro"
HOME = "/Users/ann/Library/Application Support/Maestro Career Studio"
PYTHONW = r"C:\Users\ann\AppData\Roaming\uv\tools\maestro-career-studio\Scripts\pythonw.exe"
PYTHONW_SPACES = r"C:\Users\Ann Lee\AppData\Roaming\uv\tools\maestro-career-studio\Scripts\pythonw.exe"


# --- pure rendering ------------------------------------------------------------------------

def test_plist_has_the_documented_keys():
    data = plistlib.loads(render_plist(SHIM, Path(HOME), custom_home=False))
    assert data == {
        "Label": LABEL,
        "ProgramArguments": [SHIM, "run"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "StandardOutPath": f"{HOME}/logs/launchd.log",
        "StandardErrorPath": f"{HOME}/logs/launchd.log",
    }
    assert LABEL == "io.github.seinun-ai.maestro-career-studio"


def test_plist_carries_maestro_home_only_when_the_user_set_one():
    data = plistlib.loads(render_plist(SHIM_SPACES, Path("/data/mine"), custom_home=True))
    assert data["EnvironmentVariables"] == {"MAESTRO_HOME": "/data/mine"}
    assert data["ProgramArguments"][0] == SHIM_SPACES  # a path with spaces is one argv entry


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")
def test_rendered_plist_passes_plutil_lint(tmp_path):
    target = tmp_path / "x.plist"
    target.write_bytes(render_plist(SHIM_SPACES, Path(HOME), custom_home=True))
    done = subprocess.run(["plutil", "-lint", str(target)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_unit_text_is_exact():
    text = render_unit(SHIM, Path(HOME), custom_home=False)
    assert text == (
        "[Unit]\n"
        "Description=Maestro Career Studio\n"
        "\n"
        "[Service]\n"
        f"ExecStart={SHIM} run\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def test_unit_quotes_paths_and_escapes_specifiers():
    text = render_unit('/home/ann lee/100%/$bin/maestro', Path("/srv/my data"), custom_home=True)
    assert 'ExecStart="/home/ann lee/100%%/$$bin/maestro" run\n' in text
    assert 'Environment="MAESTRO_HOME=/srv/my data"\n' in text


def test_schtasks_create_argv_runs_start_not_run():
    argv = render_schtasks_create(PYTHONW)
    assert argv == [
        "schtasks", "/Create", "/TN", "Maestro Career Studio", "/SC", "ONLOGON", "/RL", "LIMITED",
        "/F", "/TR", f"{PYTHONW} -m maestro_cs start --no-browser",
    ]
    assert TASK_NAME == "Maestro Career Studio"


def test_schtasks_quotes_the_program_when_the_path_has_spaces():
    argv = render_schtasks_create(PYTHONW_SPACES)
    assert argv[-1] == f'"{PYTHONW_SPACES}" -m maestro_cs start --no-browser'
    # The whole /TR value survives the Windows command-line round trip as one argument.
    assert subprocess.list2cmdline(argv).count("/TR") == 1


def test_schtasks_refuses_a_command_longer_than_the_scheduler_allows():
    with pytest.raises(ControlError, match="too long"):
        render_schtasks_create("C:\\" + "a" * 300 + "\\pythonw.exe")


# --- the fake system -----------------------------------------------------------------------

class Runner:
    """Records argv lists; `plan` maps a joined-argv prefix to a (returncode, text) answer."""

    def __init__(self, plan=None, missing=()):
        self.calls: list[list[str]] = []
        self.plan = plan or {}
        self.missing = set(missing)
        self.after = None

    def __call__(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] in self.missing:
            raise FileNotFoundError(argv[0])
        if self.after:
            self.after(argv)
        for prefix, (code, text) in self.plan.items():
            if " ".join(argv).startswith(prefix):
                return subprocess.CompletedProcess(argv, code, stdout="", stderr=text)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def verbs(self):
        return [" ".join(c[:3]) for c in self.calls]


def make(tmp_path, platform, runner=None, *, environ=None, **kw):
    said: list[str] = []
    obj = Autostart(
        tmp_path / "data",
        environ if environ is not None else {},
        platform=platform,
        user_home=tmp_path / "user",
        runner=runner or Runner(),
        say=said.append,
        shim=kw.pop("shim", SHIM),
        python=kw.pop("python", PYTHONW),
        which=kw.pop("which", lambda name: f"/usr/bin/{name}"),
        uid=501,
        user="ann",
        **kw,
    )
    return obj, said


def plist_file(tmp_path):
    return tmp_path / "user" / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def unit_file(tmp_path):
    return tmp_path / "user" / ".config" / "systemd" / "user" / UNIT_NAME


# --- macOS ---------------------------------------------------------------------------------

def test_mac_on_writes_a_0644_plist_and_bootstraps_it(tmp_path):
    runner = Runner()
    auto, said = make(tmp_path, "darwin", runner)
    assert auto.handle("on") == 0
    target = plist_file(tmp_path)
    assert plistlib.loads(target.read_bytes())["ProgramArguments"] == [SHIM, "run"]
    assert target.stat().st_mode & 0o777 == 0o644
    assert runner.calls == [["launchctl", "bootstrap", "gui/501", str(target)]]
    assert said == ["Autostart is on. Maestro will start when you log in; it is starting now."]
    assert (tmp_path / "data" / "logs").is_dir()  # launchd will not create the log folder


def test_mac_on_twice_does_not_touch_launchd_again(tmp_path):
    runner = Runner()
    auto, said = make(tmp_path, "darwin", runner)
    auto.handle("on")
    runner.calls.clear()
    assert auto.handle("on") == 0
    assert runner.calls == []
    assert said[-1] == "Autostart is already on."


def test_mac_on_after_a_reinstall_replaces_the_loaded_job(tmp_path):
    runner = Runner()
    make(tmp_path, "darwin", runner, shim="/old/bin/maestro")[0].handle("on")
    runner.calls.clear()
    auto, _ = make(tmp_path, "darwin", runner)
    auto.handle("on")
    assert runner.verbs() == ["launchctl bootout gui/501/" + LABEL, "launchctl bootstrap gui/501"]
    assert plistlib.loads(plist_file(tmp_path).read_bytes())["ProgramArguments"][0] == SHIM


def test_mac_bootstrap_failure_leaves_nothing_behind(tmp_path):
    runner = Runner({"launchctl bootstrap": (5, "Bootstrap failed: 5: Input/output error\n")})
    auto, _ = make(tmp_path, "darwin", runner)
    with pytest.raises(ControlError, match="Input/output error"):
        auto.handle("on")
    assert not plist_file(tmp_path).exists()


def test_mac_off_boots_out_then_deletes_the_plist(tmp_path):
    runner = Runner()
    auto, said = make(tmp_path, "darwin", runner)
    auto.handle("on")
    runner.calls.clear()
    assert auto.handle("off") == 0
    assert runner.calls == [["launchctl", "bootout", f"gui/501/{LABEL}"]]
    assert not plist_file(tmp_path).exists()
    assert said[-1] == (
        "Autostart is off. Maestro no longer starts at login and has stopped; "
        "run `maestro start` to use it now."
    )


def test_mac_off_when_the_job_was_not_loaded_still_removes_the_file(tmp_path):
    runner = Runner()
    auto, said = make(tmp_path, "darwin", runner)
    auto.handle("on")
    runner.plan = {"launchctl bootout": (3, "Boot-out failed: 3: No such process\n")}
    assert auto.handle("off") == 0
    assert not plist_file(tmp_path).exists()
    assert said[-1] == "Autostart is off. Maestro no longer starts at login."


def test_mac_off_when_already_off_calls_nothing(tmp_path):
    runner = Runner()
    auto, said = make(tmp_path, "darwin", runner)
    assert auto.handle("off") == 0
    assert runner.calls == [] and said == ["Autostart is already off."]


def test_mac_a_symlinked_plist_stays_a_link(tmp_path):
    real = tmp_path / "dotfiles" / "agent.plist"
    real.parent.mkdir()
    real.write_text("old")
    link = plist_file(tmp_path)
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    auto, _ = make(tmp_path, "darwin")
    auto.handle("on")
    assert link.is_symlink() and plistlib.loads(real.read_bytes())["Label"] == LABEL


def test_a_run_next_to_a_live_supervisor_exits_zero_so_bootstrap_is_harmless(tmp_path, monkeypatch, capsys):
    # launchd's KeepAlive only restarts after a non-zero exit; `maestro run` beside a live
    # supervisor exits 0, so bootstrapping while Maestro already runs does not loop.
    assert plistlib.loads(render_plist(SHIM, Path(HOME), False))["KeepAlive"] == {"SuccessfulExit": False}
    monkeypatch.setattr(control, "running_supervisor", lambda ctx: object())
    ctx = control.load_context({"MAESTRO_HOME": str(tmp_path / "home")})
    assert control.run(ctx) == 0
    assert "already running" in capsys.readouterr().out


# --- Linux ---------------------------------------------------------------------------------

def linux_enable_creates_link(tmp_path):
    def after(argv):
        if argv[:3] == ["systemctl", "--user", "enable"]:
            wants = unit_file(tmp_path).parent / "default.target.wants"
            wants.mkdir(exist_ok=True)
            (wants / UNIT_NAME).symlink_to(unit_file(tmp_path))
        if argv[:3] == ["systemctl", "--user", "disable"]:
            (unit_file(tmp_path).parent / "default.target.wants" / UNIT_NAME).unlink()
    return after


def test_linux_on_writes_the_unit_reloads_enables_and_lingers(tmp_path):
    runner = Runner()
    runner.after = linux_enable_creates_link(tmp_path)
    auto, said = make(tmp_path, "linux", runner)
    assert auto.handle("on") == 0
    assert unit_file(tmp_path).read_text() == render_unit(SHIM, tmp_path / "data", False)
    assert runner.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", UNIT_NAME],
        ["loginctl", "enable-linger", "ann"],
    ]
    assert said == ["Autostart is on. Maestro will start when you log in; it is not started now."]
    assert auto.is_enabled() is True


def test_linux_refused_linger_warns_but_succeeds(tmp_path):
    runner = Runner({"loginctl": (1, "Access denied\n")})
    auto, said = make(tmp_path, "linux", runner)
    assert auto.handle("on") == 0
    assert said[-1] == "Maestro will start when you log in, not at boot."


def test_linux_missing_loginctl_is_the_same_warning(tmp_path):
    auto, said = make(tmp_path, "linux", Runner(missing={"loginctl"}))
    assert auto.handle("on") == 0
    assert said[-1] == "Maestro will start when you log in, not at boot."


def test_linux_without_systemd_says_so_and_writes_nothing(tmp_path):
    auto, _ = make(tmp_path, "linux", which=lambda name: None)
    with pytest.raises(ControlError, match="needs systemd"):
        auto.handle("on")
    assert not unit_file(tmp_path).exists()


def test_linux_a_refused_enable_rolls_the_unit_back(tmp_path):
    runner = Runner({"systemctl --user enable": (1, "Failed to connect to bus: No medium found\n")})
    auto, _ = make(tmp_path, "linux", runner)
    with pytest.raises(ControlError, match="No medium found"):
        auto.handle("on")
    assert not unit_file(tmp_path).exists()


def test_linux_off_disables_deletes_and_reloads(tmp_path):
    runner = Runner()
    runner.after = linux_enable_creates_link(tmp_path)
    auto, said = make(tmp_path, "linux", runner)
    auto.handle("on")
    runner.calls.clear()
    assert auto.handle("off") == 0
    assert runner.calls == [
        ["systemctl", "--user", "disable", UNIT_NAME],
        ["systemctl", "--user", "daemon-reload"],
    ]
    assert not unit_file(tmp_path).exists()
    assert said[-1] == "Autostart is off. Maestro no longer starts at login."
    assert auto.is_enabled() is False


def test_linux_xdg_config_home_is_honoured(tmp_path):
    auto, _ = make(tmp_path, "linux", environ={"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
    auto.handle("on")
    assert (tmp_path / "xdg" / "systemd" / "user" / UNIT_NAME).is_file()


# --- Windows -------------------------------------------------------------------------------

def test_windows_on_creates_the_task_without_starting_it(tmp_path):
    runner = Runner()
    auto, said = make(tmp_path, "win32", runner)
    assert auto.handle("on") == 0
    assert runner.calls == [render_schtasks_create(PYTHONW)]
    assert said == ["Autostart is on. Maestro will start when you log in; it is not started now."]


def test_windows_uses_the_windowless_interpreter_by_default(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        autostart.runtime, "venv_python",
        lambda platform=None, windowless=False, **kw: seen.update(windowless=windowless) or Path(PYTHONW),
    )
    runner = Runner()
    obj = Autostart(tmp_path, {}, platform="win32", user_home=tmp_path, runner=runner, say=lambda *_: None)
    obj.handle("on")
    assert seen == {"windowless": True}


def test_windows_a_path_with_spaces_is_quoted(tmp_path):
    runner = Runner()
    auto, _ = make(tmp_path, "win32", runner, python=PYTHONW_SPACES)
    auto.handle("on")
    assert runner.calls[0][-1] == f'"{PYTHONW_SPACES}" -m maestro_cs start --no-browser'


def test_windows_off_deletes_the_task(tmp_path):
    runner = Runner()
    auto, said = make(tmp_path, "win32", runner)
    assert auto.handle("off") == 0
    assert runner.calls == [["schtasks", "/Query", "/TN", TASK_NAME],
                            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]]
    assert said == ["Autostart is off. Maestro no longer starts at login."]


def test_windows_off_when_there_is_no_task_calls_no_delete(tmp_path):
    runner = Runner({"schtasks /Query": (1, "ERROR: The system cannot find the file specified.")})
    auto, said = make(tmp_path, "win32", runner)
    assert auto.handle("off") == 0
    assert runner.verbs() == ["schtasks /Query /TN"] and said == ["Autostart is already off."]


def test_windows_failed_create_is_one_sentence(tmp_path):
    runner = Runner({"schtasks /Create": (1, "ERROR: Access is denied.\n")})
    auto, _ = make(tmp_path, "win32", runner)
    with pytest.raises(ControlError, match="Access is denied"):
        auto.handle("on")


def test_windows_cannot_carry_a_custom_home_and_says_so(tmp_path):
    auto, said = make(tmp_path, "win32", environ={"MAESTRO_HOME": r"D:\mine"})
    auto.handle("on")
    assert any("setx MAESTRO_HOME" in line for line in said)


# --- status and wiring ---------------------------------------------------------------------

@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_status_reports_on_and_off(tmp_path, platform):
    runner = Runner({"schtasks /Query": (1, "ERROR: not found")})
    if platform == "linux":
        runner.after = linux_enable_creates_link(tmp_path)
    auto, said = make(tmp_path, platform, runner)
    assert auto.handle("status") == 0
    assert said[-1] == "Autostart is off."
    runner.plan = {}
    auto.handle("on")
    assert auto.is_enabled() is True
    assert auto.handle("status") == 0
    assert said[-1] == "Autostart is on."


def test_status_is_unknown_when_windows_has_no_schtasks(tmp_path):
    auto, said = make(tmp_path, "win32", Runner(missing={"schtasks"}))
    assert auto.is_enabled() is None
    auto.handle("status")
    assert said[-1] == "Autostart: can't tell on this computer."


def test_a_timed_out_system_call_is_a_sentence(tmp_path):
    def slow(argv):
        raise subprocess.TimeoutExpired(argv, 30)
    auto, _ = make(tmp_path, "darwin", slow)
    with pytest.raises(ControlError, match="did not answer"):
        auto.handle("on")
    assert not plist_file(tmp_path).exists()


def test_unsupported_platform_is_a_sentence(tmp_path):
    auto, _ = make(tmp_path, "freebsd14")
    with pytest.raises(ControlError, match="isn't supported"):
        auto.handle("on")


def test_status_json_wiring_uses_is_enabled(monkeypatch):
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: True)
    assert control.autostart_enabled() is True


def test_module_is_enabled_reads_the_real_layout_from_injected_dirs(tmp_path):
    target = plist_file(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    # conftest stubs the module-level is_enabled; go through the class it wraps.
    obj = Autostart(tmp_path, {}, platform="darwin", user_home=tmp_path / "user", runner=Runner())
    assert obj.is_enabled() is True


# --- the command line ----------------------------------------------------------------------

def test_cli_routes_autostart_actions(monkeypatch, tmp_path):
    seen = []

    class Fake:
        def __init__(self, home, environ, **kw):
            seen.append(("init", home))

        def handle(self, action):
            seen.append(action)
            return 0

    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(autostart, "Autostart", Fake)
    for action in ("on", "off", "status"):
        assert cli.main(["autostart", action]) == 0
    assert [s for s in seen if isinstance(s, str)] == ["on", "off", "status"]


def test_cli_rejects_other_actions(capsys):
    assert cli.main(["autostart", "maybe"]) == 2
    assert "invalid choice" in capsys.readouterr().err


def test_cli_prints_a_control_error_as_one_sentence(monkeypatch, tmp_path, capsys):
    class Boom:
        def __init__(self, *a, **k): ...
        def handle(self, action):
            raise ControlError("Autostart needs systemd.")

    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(autostart, "Autostart", Boom)
    assert cli.main(["autostart", "on"]) == 1
    assert capsys.readouterr().err.strip() == "Autostart needs systemd."
