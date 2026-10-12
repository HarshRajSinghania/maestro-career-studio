"""The native folder dialog: argv per OS, cancel mapping, escaping and the single-dialog lock.

subprocess.run is always faked here. A real dialog must never open in a test.
"""
import subprocess
from pathlib import Path

import pytest

from app.services import folder_picker as fp


class Ran:
    def __init__(self, returncode=0, stdout="", stderr="", raises=None):
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.raises = raises
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.raises:
            raise self.raises
        return self.result


@pytest.fixture
def run(monkeypatch):
    ran = Ran()
    monkeypatch.setattr(fp.subprocess, "run", ran)
    return ran


@pytest.fixture
def start(tmp_path):
    folder = tmp_path / "Documents"
    folder.mkdir()
    return folder


def test_macos_argv_has_the_default_location(run, start):
    run.result.stdout = f"{start}/\n"
    assert fp.choose(start, platform="darwin") == start
    (cmd, kwargs), = run.calls
    assert cmd[:2] == ["osascript", "-e"]
    assert cmd[2].startswith("POSIX path of (choose folder with prompt ")
    assert f'default location (POSIX file "{start}")' in cmd[2]
    assert kwargs["timeout"] == 300
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert "shell" not in kwargs


def test_macos_leaves_out_the_default_location_when_the_start_is_missing(run, tmp_path):
    run.result.stdout = "/Users/me/Documents/\n"
    fp.choose(tmp_path / "gone", platform="darwin")
    assert "default location" not in run.calls[0][0][2]


def test_macos_escapes_quotes_and_backslashes_in_the_path(run, tmp_path):
    odd = tmp_path / 'a"b\\c'
    odd.mkdir()
    run.result.stdout = "/x/\n"
    fp.choose(odd, platform="darwin")
    script = run.calls[0][0][2]
    assert f'(POSIX file "{str(odd).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}")' in script
    assert 'a\\"b\\\\c' in script


def test_macos_trailing_slash_is_dropped(run, start):
    run.result.stdout = "/Users/ajeyds/Documents/\n"
    assert fp.choose(start, platform="darwin") == Path("/Users/ajeyds/Documents")


def test_macos_cancel_is_none(run, start):
    run.result.returncode = 1
    run.result.stderr = "execution error: User canceled. (-128)"
    assert fp.choose(start, platform="darwin") is None


def test_macos_other_failure_raises(run, start):
    run.result.returncode = 1
    run.result.stderr = "execution error: something else (-1700)"
    with pytest.raises(fp.PickerFailed):
        fp.choose(start, platform="darwin")


def test_windows_argv_passes_the_start_in_the_environment(run, start):
    run.result.stdout = "C:\\Users\\me\\Resumes\r\n"
    chosen = fp.choose(start, platform="win32")
    assert chosen == Path("C:\\Users\\me\\Resumes")
    (cmd, kwargs), = run.calls
    assert cmd[:4] == ["powershell", "-NoProfile", "-STA", "-Command"]
    assert "FolderBrowserDialog" in cmd[4]
    assert str(start) not in cmd[4]  # never interpolated into the script
    assert kwargs["env"][fp.START_ENV] == str(start)


def test_windows_cancel_is_empty_output(run, start):
    run.result.stdout = ""
    assert fp.choose(start, platform="win32") is None


def test_windows_failure_raises(run, start):
    run.result.returncode = 1
    with pytest.raises(fp.PickerFailed):
        fp.choose(start, platform="win32")


def test_linux_argv_and_trailing_slash(run, start):
    run.result.stdout = f"{start}/\n"
    assert fp.choose(start, platform="linux") == start
    cmd = run.calls[0][0]
    assert cmd[:3] == ["zenity", "--file-selection", "--directory"]
    assert f"--filename={start}/" in cmd


def test_linux_cancel_is_none_and_other_codes_fail(run, start):
    run.result.returncode = 1
    assert fp.choose(start, platform="linux") is None
    run.result.returncode = 255
    with pytest.raises(fp.PickerFailed):
        fp.choose(start, platform="linux")


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("osascript", 300), FileNotFoundError("osascript")])
@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_a_timeout_or_missing_program_is_picker_failed(monkeypatch, start, error, platform):
    monkeypatch.setattr(fp.subprocess, "run", Ran(raises=error))
    with pytest.raises(fp.PickerFailed):
        fp.choose(start, platform=platform)
    assert fp._lock.acquire(blocking=False)  # released after a failure
    fp._lock.release()


def test_a_second_dialog_while_one_is_open_is_busy(run, start):
    assert fp._lock.acquire(blocking=False)
    try:
        with pytest.raises(fp.PickerBusy):
            fp.choose(start, platform="darwin")
    finally:
        fp._lock.release()
    assert run.calls == []


def test_the_lock_is_released_after_a_normal_answer(run, start):
    run.result.stdout = "/x\n"
    fp.choose(start, platform="darwin")
    fp.choose(start, platform="darwin")
    assert len(run.calls) == 2


def test_available_per_platform(monkeypatch):
    monkeypatch.setattr(fp.shutil, "which", lambda name: f"/bin/{name}")
    assert fp.available("darwin") and fp.available("win32") and fp.available("linux")
    monkeypatch.setattr(fp.shutil, "which", lambda name: None)
    assert not fp.available("darwin") and not fp.available("win32") and not fp.available("linux")
    seen = []
    monkeypatch.setattr(fp.shutil, "which", lambda name: seen.append(name) or None)
    for platform in ("darwin", "win32", "linux"):
        fp.available(platform)
    assert seen == ["osascript", "powershell", "zenity"]
