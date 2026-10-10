"""`maestro update`: version logic, changelog parsing, ordering, the Windows handoff.

No network, no uv, no real processes: every collaborator is a fake that records the order.
"""
import json
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from maestro_cs import cli, control, detach, update
from maestro_cs.control import Context, ControlError
from maestro_cs.home import Ports

CHANGELOG = """# Changelog

## [Unreleased]

### Breaking changes

- Not released yet.

## [0.9.0] — 2026-10-20

### Added

- Something nice.

### Breaking changes

- The nine-oh note.

  Second paragraph of it.

### Fixed

- A bug.

## [0.8.1] — 2026-10-12

### Fixed

- No breaking section here.

## [0.8.0] — 2026-10-07

### Breaking changes

- None that need an action from you.

## [0.7.0] — 2026-10-01

### Breaking changes

- The seven-oh note (already installed).
"""


# --- versions ------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("0.9.0", (0, 9, 0)), ("v1.2.3", (1, 2, 3)), (" 10.20.30 ", (10, 20, 30)),
    ("1.0.0rc1", None), ("1.0", None), ("1.0.0.1", None), ("dev", None), ("", None), (None, None),
    ("1.0.0+local", None), ("1.0.0-beta", None),
])
def test_parse_version(text, expected):
    assert update.parse_version(text) == expected


def test_versions_compare_numerically_not_as_text():
    assert update.is_newer("0.10.0", "0.9.0")
    assert update.is_newer("1.0.0", "0.99.99")
    assert not update.is_newer("0.9.0", "0.9.0")
    assert not update.is_newer("0.8.9", "0.9.0")


def test_a_prerelease_or_unparseable_version_is_never_newer():
    assert not update.is_newer("1.0.0rc1", "0.9.0")
    assert not update.is_newer("0.9.1", "dev")
    assert not update.is_newer(None, "0.9.0")


def pypi(info_version, releases=None):
    body = json.dumps({"info": {"version": info_version}, "releases": releases or {}})
    seen = []

    def fetch(url, timeout):
        seen.append((url, timeout))
        return body

    return fetch, seen


def test_latest_version_reads_info_version_with_a_3_second_timeout():
    fetch, seen = pypi("0.9.0")
    assert update.latest_version(fetch=fetch) == "0.9.0"
    assert seen == [("https://pypi.org/pypi/maestro-career-studio/json", 3.0)]


def test_latest_version_ignores_a_prerelease_head():
    fetch, _ = pypi("1.0.0rc1", {"0.9.0": [{}], "0.9.1": [{"yanked": True}], "1.0.0rc1": [{}],
                                 "0.10.0": [{}, {"yanked": True}]})
    assert update.latest_version(fetch=fetch) == "0.10.0"
    fetch, _ = pypi("1.0.0rc1", {"1.0.0rc1": [{}]})
    assert update.latest_version(fetch=fetch) is None


@pytest.mark.parametrize("body", [None, "not json", "[]", '{"info": {}}', '{"info": 3}'])
def test_latest_version_is_none_on_any_failure(body):
    assert update.latest_version(fetch=lambda url, timeout: body) is None


@pytest.mark.parametrize("releases", [{"0.9.0": [1]}, {"0.9.0": 5}, {"0.9.0": None}, [], 7,
                                      {"0.9.0": [{}], "0.9.1": "x"}])
def test_malformed_releases_never_raise(releases):
    body = json.dumps({"info": {"version": "1.0.0rc1"}, "releases": releases})
    result = update.latest_version(fetch=lambda url, timeout: body)
    assert result in (None, "0.9.0")


def test_control_latest_version_delegates_to_update(monkeypatch):
    monkeypatch.setattr(update, "latest_version", lambda timeout=3.0, fetch=None: f"t{timeout}")
    assert control.latest_version(1.5) == "t1.5"


def test_fetch_text_returns_none_without_a_network(monkeypatch, real_fetch_text):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", boom)
    assert real_fetch_text("https://pypi.org/x") is None


def test_fetch_text_uses_the_proxy_environment_and_rejects_non_200(monkeypatch, real_fetch_text):
    import httpx

    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(404, text="nope")

    monkeypatch.setattr(httpx, "get", fake_get)
    assert real_fetch_text("https://pypi.org/x") is None
    assert calls[0]["trust_env"] is True and calls[0]["timeout"] == 3.0


# --- changelog ------------------------------------------------------------------------------

def test_breaking_notes_picks_only_current_exclusive_to_new_inclusive():
    notes = update.parse_breaking(CHANGELOG, "0.7.0", "0.9.0")
    assert [v for v, _ in notes] == ["0.9.0", "0.8.0"]  # not 0.7.0, not Unreleased, 0.8.1 has none
    assert notes[0][1].startswith("- The nine-oh note.")
    assert "Second paragraph of it." in notes[0][1]
    assert "A bug" not in notes[0][1] and "Something nice" not in notes[0][1]


def test_breaking_notes_stop_at_the_new_version():
    assert [v for v, _ in update.parse_breaking(CHANGELOG, "0.7.0", "0.8.0")] == ["0.8.0"]
    assert update.parse_breaking(CHANGELOG, "0.9.0", "0.9.0") == []


def test_breaking_notes_fetches_the_tagged_changelog_and_none_on_failure():
    seen = []

    def fetch(url, timeout):
        seen.append(url)
        return CHANGELOG

    assert update.breaking_notes("0.8.0", "0.9.0", fetch)
    assert seen == ["https://raw.githubusercontent.com/seinun-ai/maestro-career-studio/v0.9.0/CHANGELOG.md"]
    assert update.breaking_notes("0.8.0", "0.9.0", lambda u, t: None) is None


# --- finding uv ------------------------------------------------------------------------------

def test_find_uv_prefers_path_then_local_bin_then_cargo(tmp_path):
    home = tmp_path / "h"
    (home / ".cargo" / "bin").mkdir(parents=True)
    (home / ".cargo" / "bin" / "uv").write_text("x")
    assert update.find_uv({}, "linux", lambda n: "/opt/uv", home) == Path("/opt/uv")
    assert update.find_uv({}, "linux", lambda n: None, home) == home / ".cargo" / "bin" / "uv"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "uv").write_text("x")
    assert update.find_uv({}, "linux", lambda n: None, home) == home / ".local" / "bin" / "uv"


def test_find_uv_none_and_windows_names(tmp_path):
    assert update.find_uv({}, "linux", lambda n: None, tmp_path) is None
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    (tmp_path / ".local" / "bin" / "uv.exe").write_text("x")
    assert update.find_uv({"USERPROFILE": str(tmp_path)}, "win32", lambda n: None) == (
        tmp_path / ".local" / "bin" / "uv.exe")


def test_upgrade_commands():
    uv = Path("/x/uv")
    assert update.upgrade_argv(uv, None) == ["/x/uv", "tool", "upgrade", "maestro-career-studio"]
    assert update.upgrade_argv(uv, "/w/m.whl") == [
        "/x/uv", "tool", "install", "--force", "--python", "3.12", "/w/m.whl"]
    assert update.rollback_command("0.8.0") == (
        "uv tool install --force --python 3.12 maestro-career-studio==0.8.0")
    assert update.wheel_version("/d/maestro_career_studio-0.9.0-py3-none-any.whl") == "0.9.0"
    assert update.wheel_version("/d/other.whl") is None


@pytest.mark.parametrize("name,expected", [
    ("maestro_career_studio-0.0.0.dev2-py3-none-any.whl", "0.0.0.dev2"),
    ("maestro_career_studio-0.9.0rc1-py3-none-any.whl", "0.9.0rc1"),
    ("maestro_career_studio-0.9.0+local.1-py3-none-any.whl", "0.9.0+local.1"),
    ("Maestro_Career_Studio-1.0.0-py3-none-any.whl", "1.0.0"),
    ("maestro_career_studio-.whl", None),
])
def test_wheel_version_reads_any_pep_440_name(name, expected):
    assert update.wheel_version(f"/d/{name}") == expected


# --- the flow ----------------------------------------------------------------------------------

class Rig:
    """An Updater wired to fakes; `events` records the order of the side effects."""

    def __init__(self, tmp_path, monkeypatch, *, version="0.8.0", latest="0.9.0", running=True,
                 backup_error=None, db=True, uv="/bin/uv", uv_code=0, answers=("y",),
                 platform="linux", served_version=None, installed="0.9.0", changelog=CHANGELOG,
                 start_code=0):
        home = tmp_path / "home"
        for name in ("logs", "run", "data"):
            (home / name).mkdir(parents=True, exist_ok=True)
        self.ctx = Context(home, {}, Ports(18001, 13000), {"PATH": "/usr/bin"}, version)
        self.events, self.said, self.asked = [], [], []
        self.popen_calls, self.run_calls = [], []
        self.answers = list(answers)
        self.uv_code, self.installed, self.start_code = uv_code, installed, start_code
        self.served_version = served_version if served_version is not None else latest
        rig = self
        pypi_body = json.dumps({"info": {"version": latest}}) if latest else None

        def fetch(url, timeout):
            rig.events.append(f"GET {url}")
            return pypi_body if url.startswith("https://pypi.org") else changelog

        class FakeMaintenance:
            def __init__(self, ctx, say=print, **kw):
                self.say = say

            def backup(self, out=None):
                rig.events.append("backup")
                if backup_error:
                    raise ControlError(backup_error)
                self.say(str(home / "backups" / "db-1.sqlite3.gz") if db else "Nothing to back up yet.")
                return 0

        class FakeController:
            def __init__(self, ctx, **kw):
                pass

            def stop(self):
                rig.events.append("stop")
                return 0

        monkeypatch.setattr(control, "running_supervisor", lambda ctx: object() if running else None)
        self.up = update.Updater(
            self.ctx, fetch=fetch, run=self.run, popen=self.popen, ask=self.ask,
            say=lambda *a, **k: self.said.append(" ".join(str(x) for x in a)),
            make_maintenance=FakeMaintenance, make_controller=FakeController,
            which=lambda name: uv, http_get=self.http_get, platform=platform,
            sleep=lambda s: None, launcher_pids=lambda: [4242, 4243],
            preload=lambda: self.events.append("preload"), user_home=tmp_path / "nohome",
        )

    def ask(self, prompt):
        self.asked.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def run(self, argv, **kwargs):
        self.run_calls.append((argv, kwargs))
        name = Path(argv[0]).name
        if name == "uv":
            self.events.append("uv")
            return subprocess.CompletedProcess(argv, self.uv_code, stdout="uv out",
                                               stderr="uv err" if self.uv_code else "")
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"maestro-career-studio {self.installed}\n", stderr="")
        self.events.append("start")
        return subprocess.CompletedProcess(argv, self.start_code, stdout="Maestro is running: http://localhost:13000\n", stderr="")

    def popen(self, argv, **kwargs):
        self.events.append("handoff")
        self.popen_calls.append((argv, kwargs))
        return object()

    def http_get(self, url, timeout=2.0):
        self.events.append("verify")
        return {"version": self.served_version}

    @property
    def text(self):
        return "\n".join(self.said)


def test_up_to_date_says_so_and_changes_nothing(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, version="0.9.0", latest="0.9.0")
    assert rig.up.run() == 0
    assert "Maestro 0.9.0 is the newest version." in rig.text
    assert "backup" not in rig.events and rig.asked == []


def test_a_failed_check_is_one_sentence_and_changes_nothing(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, latest=None)
    with pytest.raises(ControlError, match="Couldn't check for a newer version"):
        rig.up.run()
    assert not any(e in rig.events for e in ("backup", "stop", "uv"))


def test_a_source_checkout_has_nothing_to_update(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, version="dev")
    with pytest.raises(ControlError, match="isn't a released install"):
        rig.up.run()


def test_the_happy_path_orders_snapshot_stop_uv_start_verify(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    assert rig.up.run() == 0
    steps = [e for e in rig.events if not e.startswith("GET")]
    assert steps == ["backup", "stop", "preload", "uv", "start", "verify"]
    assert rig.asked == ["Update from 0.8.0 to 0.9.0? [y/N] "]
    assert "Maestro is now version 0.9.0." in rig.text
    uv_argv = next(a for a, _ in rig.run_calls if Path(a[0]).name == "uv")
    assert uv_argv[1:] == ["tool", "upgrade", "maestro-career-studio"]
    start_argv = next(a for a, _ in rig.run_calls if a[1:] == ["start", "--no-browser"])
    assert Path(start_argv[0]).name == "maestro"  # the shim, a NEW process (this one has old code)


def test_both_network_calls_are_named_before_they_are_made(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    rig.up.run()
    assert "pypi.org" in rig.text and "raw.githubusercontent.com" in rig.text
    assert "The nine-oh note." in rig.text and "0.9.0" in rig.text


def test_changelog_failure_is_a_pointer_and_the_update_still_asks(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, changelog=None)
    assert rig.up.run() == 0
    assert "Couldn't read the changelog; see https://github.com/seinun-ai/maestro-career-studio/blob/v0.9.0/CHANGELOG.md." in rig.text
    assert rig.asked


def test_yes_skips_the_prompt(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, answers=())
    assert rig.up.run(yes=True) == 0
    assert rig.asked == [] and "uv" in rig.events


def test_declining_changes_nothing(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, answers=("n",))
    assert rig.up.run() == 0
    assert not any(e in rig.events for e in ("backup", "stop", "uv"))
    assert "Nothing changed." in rig.text


def test_no_one_to_ask_points_at_yes_and_changes_nothing(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, answers=())
    with pytest.raises(ControlError, match="--yes"):
        rig.up.run()
    assert not any(e in rig.events for e in ("backup", "stop", "uv"))


def test_a_failed_snapshot_aborts_with_nothing_stopped_and_no_uv(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, backup_error="The backup failed: disk full.")
    with pytest.raises(ControlError, match="The backup failed: disk full."):
        rig.up.run(yes=True)
    steps = [e for e in rig.events if not e.startswith("GET")]
    assert steps == ["backup"]
    assert rig.popen_calls == [] and rig.run_calls == []


def test_missing_uv_prints_the_manual_command_before_touching_anything(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, uv=None)
    assert rig.up.run(yes=True) == 1
    assert "`uv tool upgrade maestro-career-studio`" in rig.text
    assert "https://docs.astral.sh/uv/" in rig.text and "Nothing was changed" in rig.text
    assert not any(e in rig.events for e in ("backup", "stop", "uv"))


def test_a_stopped_maestro_is_not_started_and_the_version_comes_from_the_shim(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, running=False)
    assert rig.up.run(yes=True) == 0
    steps = [e for e in rig.events if not e.startswith("GET")]
    assert steps == ["backup", "preload", "uv"]
    assert "Start it with `maestro start`." in rig.text


def test_no_database_yet_skips_the_snapshot_quietly(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, db=False)
    assert rig.up.run(yes=True) == 0
    assert "nothing to snapshot" in rig.text


def test_a_failed_upgrade_prints_uvs_output_and_restarts_the_old_version(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, uv_code=2)
    assert rig.up.run(yes=True) == 1
    steps = [e for e in rig.events if not e.startswith("GET")]
    assert steps == ["backup", "stop", "preload", "uv", "start"]
    assert "uv out" in rig.text and "uv err" in rig.text and "data is untouched" in rig.text
    assert "Starting the previous version again" in rig.text
    assert "update.log" not in rig.text and (rig.ctx.logs_dir / "update.log").is_file()


def test_a_failed_upgrade_while_stopped_starts_nothing(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, running=False, uv_code=2)
    assert rig.up.run(yes=True) == 1
    assert "start" not in rig.events


def test_a_version_mismatch_prints_the_snapshot_and_the_rollback_line(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, served_version="0.8.0")
    assert rig.up.run(yes=True) == 1
    assert "Maestro is on version 0.8.0, not the 0.9.0 that was expected." in rig.text
    assert str(rig.ctx.home / "backups" / "db-1.sqlite3.gz") in rig.text
    assert "uv tool install --force --python 3.12 maestro-career-studio==0.8.0" in rig.text
    assert "only run forward" in rig.text


def test_a_new_version_that_fails_to_start_says_so_not_unknown(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, start_code=1)
    assert rig.up.run(yes=True) == 1
    assert "Maestro didn't start after the update." in rig.text
    assert "unknown" not in rig.text and "not the 0.9.0 that was expected" not in rig.text
    assert str(rig.ctx.home / "backups" / "db-1.sqlite3.gz") in rig.text
    assert "uv tool install --force --python 3.12 maestro-career-studio==0.8.0" in rig.text
    assert "verify" not in rig.events


def test_a_dev_wheel_gets_the_post_install_check(tmp_path, monkeypatch):
    wheel = tmp_path / "maestro_career_studio-0.0.0.dev2-py3-none-any.whl"
    wheel.write_bytes(b"w")
    rig = Rig(tmp_path, monkeypatch, version="0.8.0", served_version="0.8.0")
    assert rig.up.run(source=str(wheel), yes=True) == 1
    assert "Maestro is on version 0.8.0, not the 0.0.0.dev2 that was expected." in rig.text
    ok = Rig(tmp_path, monkeypatch, version="0.8.0", served_version="0.0.0.dev2")
    assert ok.up.run(source=str(wheel), yes=True) == 0
    assert "Maestro is now version 0.0.0.dev2." in ok.text


def test_a_mismatch_while_stopped_uses_the_installed_version(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, running=False, installed="0.8.0")
    assert rig.up.run(yes=True) == 1
    assert "not the 0.9.0 that was expected" in rig.text and "verify" not in rig.events


def test_source_installs_the_wheel_and_skips_the_network(tmp_path, monkeypatch):
    wheel = tmp_path / "maestro_career_studio-0.9.0-py3-none-any.whl"
    wheel.write_bytes(b"w")
    rig = Rig(tmp_path, monkeypatch, version="0.9.0", installed="0.9.0")
    assert rig.up.run(source=str(wheel), yes=True) == 0  # same version: --source still installs
    assert not [e for e in rig.events if e.startswith("GET")]
    uv_argv = next(a for a, _ in rig.run_calls if Path(a[0]).name == "uv")
    assert uv_argv[1:] == ["tool", "install", "--force", "--python", "3.12", str(wheel)]


def test_a_relative_source_is_made_absolute_before_uv_runs_from_another_folder(tmp_path, monkeypatch):
    (tmp_path / "dl").mkdir()
    (tmp_path / "dl" / "maestro_career_studio-0.9.0-py3-none-any.whl").write_bytes(b"w")
    monkeypatch.chdir(tmp_path)
    rig = Rig(tmp_path, monkeypatch)
    assert rig.up.run(source="dl/maestro_career_studio-0.9.0-py3-none-any.whl", yes=True) == 0
    uv_argv, kwargs = next((a, k) for a, k in rig.run_calls if Path(a[0]).name == "uv")
    expected = (tmp_path / "dl" / "maestro_career_studio-0.9.0-py3-none-any.whl").resolve()
    assert uv_argv[-1] == str(expected) and Path(uv_argv[-1]).is_absolute()
    assert kwargs["cwd"] == rig.ctx.home  # not the caller's folder, which is why it must be absolute


def test_a_tilde_source_is_expanded_before_uv_gets_it(tmp_path, monkeypatch):
    fake_home = tmp_path / "me"
    fake_home.mkdir()
    (fake_home / "maestro_career_studio-0.9.0-py3-none-any.whl").write_bytes(b"w")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    rig = Rig(tmp_path, monkeypatch)
    assert rig.up.run(source="~/maestro_career_studio-0.9.0-py3-none-any.whl", yes=True) == 0
    uv_argv = next(a for a, _ in rig.run_calls if Path(a[0]).name == "uv")
    assert "~" not in uv_argv[-1]
    assert uv_argv[-1] == str((fake_home / "maestro_career_studio-0.9.0-py3-none-any.whl").resolve())


def test_a_relative_source_reaches_the_windows_script_absolute(tmp_path, monkeypatch):
    (tmp_path / "maestro_career_studio-0.9.0-py3-none-any.whl").write_bytes(b"w")
    monkeypatch.chdir(tmp_path)
    rig = Rig(tmp_path, monkeypatch, platform="win32", uv=WIN_UV)
    assert rig.up.run(source="maestro_career_studio-0.9.0-py3-none-any.whl", yes=True) == 0
    script = (rig.ctx.logs_dir / "update.cmd").read_text(encoding="utf-8")
    assert str((tmp_path / "maestro_career_studio-0.9.0-py3-none-any.whl").resolve()) in script


def test_source_must_exist(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    with pytest.raises(ControlError, match="There is no file at"):
        rig.up.run(source=str(tmp_path / "missing.whl"), yes=True)
    assert "backup" not in rig.events


def test_preload_imports_what_the_post_upgrade_path_needs():
    update._preload()
    for name in ("webbrowser", "urllib.request", "json", "subprocess", "encodings.idna",
                 "http.client", "maestro_cs.autostart", "maestro_cs.procs", "maestro_cs.control"):
        assert name in sys.modules


# --- Windows -----------------------------------------------------------------------------------

WIN_UV = r"C:\Users\Jo Doe\.local\bin\uv.exe"
WIN_SHIM = Path(r"C:\Users\Jo Doe\AppData\Roaming\uv\tools\maestro-career-studio\Scripts\maestro.exe")


def test_windows_script_quotes_every_path_and_waits_before_uv():
    script = update.windows_script(
        wait_pids=[4242, 4243], uv_argv=update.upgrade_argv(Path(WIN_UV), None), shim=WIN_SHIM,
        restart=True, snapshot=r"C:\Users\Jo Doe\100%\db (1).gz", old="0.8.0")
    lines = script.split("\r\n")
    assert lines[0] == "@echo off" and script.endswith("\r\n")
    assert 'tasklist /FI "PID eq 4242" /NH /FO CSV 2>nul | find ",""4242""," >nul' in lines
    assert 'tasklist /FI "PID eq 4243" /NH /FO CSV 2>nul | find ",""4243""," >nul' in lines
    assert "if %tries0% GEQ 30 goto done0" in lines
    uv_line = r'"C:\Users\Jo Doe\.local\bin\uv.exe" "tool" "upgrade" "maestro-career-studio"'
    assert uv_line in lines
    assert lines.index('tasklist /FI "PID eq 4242" /NH /FO CSV 2>nul | find ",""4242""," >nul') < lines.index(uv_line)
    assert f'"{WIN_SHIM}" start --no-browser' in lines
    assert lines.index(uv_line) < lines.index(f'"{WIN_SHIM}" start --no-browser')
    assert lines.index(":failed") < lines.index(":restart") < lines.index(f'"{WIN_SHIM}" start --no-browser')
    # the snapshot path reaches the log with cmd's special characters escaped
    assert r"echo [update] Snapshot saved first: C:\Users\Jo Doe\100%%\db ^(1^).gz" in lines
    assert "echo [update] To pin the old version: uv tool install --force --python 3.12 maestro-career-studio==0.8.0" in lines


def test_windows_script_doubles_percent_in_the_uv_line_and_checks_its_exit_code_next():
    uv = r"C:\Users\100%\.local\bin\uv.exe"
    script = update.windows_script(wait_pids=[1], uv_argv=update.upgrade_argv(Path(uv), r"C:\w\50%.whl"),
                                   shim=WIN_SHIM, restart=False, snapshot=None, old="0.8.0")
    lines = script.split("\r\n")
    uv_line = next(ln for ln in lines if "uv.exe" in ln)
    assert uv_line == (r'"C:\Users\100%%\.local\bin\uv.exe" "tool" "install" "--force" "--python" "3.12" '
                       r'"C:\w\50%%.whl"')
    assert lines[lines.index(uv_line) + 1] == "if errorlevel 1 goto failed"


def test_windows_script_without_a_running_maestro_does_not_start_it():
    script = update.windows_script(wait_pids=[1], uv_argv=["uv", "x"], shim=WIN_SHIM,
                                   restart=False, snapshot=None, old="0.8.0")
    assert "start --no-browser" not in script and "Snapshot saved" not in script


class FakeProc:
    def __init__(self, pid, name, parent=None, gone=False):
        self.pid, self._name, self._parent, self._gone = pid, name, parent, gone

    def name(self):
        if self._gone:
            raise psutil.NoSuchProcess(self.pid)
        return self._name

    def parent(self):
        return self._parent


def own_pids(proc):
    return update.Updater._own_pids(lambda: proc)


def test_launcher_chain_under_uv_waits_on_both_ancestors(monkeypatch):
    monkeypatch.setattr(update.os, "getpid", lambda: 300)
    tree = FakeProc(300, "python.exe", FakeProc(200, "PYTHON.EXE", FakeProc(100, "maestro.exe",
                    FakeProc(50, "python.exe", FakeProc(1, "explorer.exe")))))
    assert own_pids(tree) == [300, 200, 100]  # at most two ancestors, the grandparent included


def test_launcher_walk_stops_at_a_shell(monkeypatch):
    monkeypatch.setattr(update.os, "getpid", lambda: 300)
    assert own_pids(FakeProc(300, "python.exe", FakeProc(200, "cmd.exe", FakeProc(100, "maestro.exe")))) == [300]
    assert own_pids(FakeProc(300, "python.exe", FakeProc(200, "Maestro.exe", FakeProc(100, "pwsh.exe")))) == [300, 200]
    assert own_pids(FakeProc(300, "python.exe", None)) == [300]


def test_launcher_walk_survives_a_vanished_parent(monkeypatch):
    monkeypatch.setattr(update.os, "getpid", lambda: 300)
    assert own_pids(FakeProc(300, "python.exe", FakeProc(200, "python.exe", gone=True))) == [300]


def test_windows_handoff_argv_uses_a_bare_script_name():
    assert update.windows_handoff_argv() == ["cmd", "/d", "/c", ".\\update.cmd"]


def test_windows_update_stops_then_hands_off_without_running_uv(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, platform="win32", uv=WIN_UV)
    assert rig.up.run(yes=True) == 0
    steps = [e for e in rig.events if not e.startswith("GET")]
    assert steps == ["backup", "stop", "handoff"]  # uv never runs in this process; no preload needed
    assert rig.run_calls == []
    argv, kwargs = rig.popen_calls[0]
    assert argv == ["cmd", "/d", "/c", ".\\update.cmd"]
    assert kwargs["cwd"] == rig.ctx.logs_dir
    assert kwargs["creationflags"] == detach.CREATE_NEW_PROCESS_GROUP | detach.CREATE_NO_WINDOW
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.STDOUT
    assert Path(kwargs["stdout"].name) == rig.ctx.logs_dir / "update.log"
    script = (rig.ctx.logs_dir / "update.cmd").read_text(encoding="utf-8")
    assert "4242" in script and "4243" in script and WIN_UV in script
    assert "Updating in the background; it takes about a minute. Don't run `maestro` until" in rig.text
    assert "update.log ends with \"finished\", then run `maestro status`." in rig.text


def test_windows_handoff_failure_says_maestro_is_stopped(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, platform="win32", uv=WIN_UV)

    def boom(argv, **kwargs):
        raise OSError(2, "No such file", "cmd")

    rig.up.popen = boom
    with pytest.raises(ControlError, match="run `maestro start`"):
        rig.up.run(yes=True)


# --- the command line --------------------------------------------------------------------------

def test_cli_update_flags_reach_the_updater(monkeypatch, tmp_path):
    calls = []

    class FakeUpdater:
        def __init__(self, ctx):
            pass

        def run(self, source, yes):
            calls.append((source, yes))
            return 0

    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(update, "Updater", FakeUpdater)
    assert cli.main(["update"]) == 0
    assert cli.main(["update", "--yes", "--source", "w.whl"]) == 0
    assert cli.main(["update", "-y"]) == 0
    assert calls == [(None, False), ("w.whl", True), (None, True)]
