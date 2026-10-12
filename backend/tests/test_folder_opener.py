"""The OS file-manager launcher: commands per platform, and how it starts them."""
import subprocess
from pathlib import Path

import pytest

from app.services import folder_opener

FILE = Path("/Users/me/Documents/Maestro/Acme (2026-10-11)/Resume.pdf")
FOLDER = FILE.parent


def test_macos_commands():
    assert folder_opener.command(FILE, select=True, platform="darwin") == ["open", "-R", str(FILE)]
    assert folder_opener.command(FOLDER, select=False, platform="darwin") == ["open", str(FOLDER)]


def test_windows_commands_are_strings_with_the_select_switch_unquoted():
    # list2cmdline would quote "/select,<path>" whole, and Explorer then ignores the selection.
    assert folder_opener.command(FILE, select=True, platform="win32") == f'explorer /select,"{FILE}"'
    assert folder_opener.command(FOLDER, select=False, platform="win32") == f'explorer "{FOLDER}"'


def test_linux_commands_open_the_parent_for_a_file():
    # xdg-open cannot select a file, so the file's folder is opened.
    assert folder_opener.command(FILE, select=True, platform="linux") == ["xdg-open", str(FOLDER)]
    assert folder_opener.command(FOLDER, select=False, platform="linux") == ["xdg-open", str(FOLDER)]


def _record_popen(monkeypatch):
    calls = []

    def fake(cmd, **kwargs):
        calls.append((cmd, kwargs))

    monkeypatch.setattr(folder_opener.subprocess, "Popen", fake)
    return calls


def test_reveal_starts_the_command_without_a_shell(monkeypatch):
    calls = _record_popen(monkeypatch)
    monkeypatch.setattr(folder_opener.sys, "platform", "darwin")
    folder_opener.reveal(FILE)
    (cmd, kwargs), = calls
    assert cmd == folder_opener.command(FILE, select=True)
    assert "shell" not in kwargs
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["close_fds"] is True


def test_open_folder_starts_the_folder_command(monkeypatch):
    calls = _record_popen(monkeypatch)
    folder_opener.open_folder(FOLDER)
    (cmd, _), = calls
    assert cmd == folder_opener.command(FOLDER, select=False)


def test_a_missing_program_is_opener_unavailable(monkeypatch):
    def boom(*_a, **_k):
        raise FileNotFoundError("open")

    monkeypatch.setattr(folder_opener.subprocess, "Popen", boom)
    with pytest.raises(folder_opener.OpenerUnavailable):
        folder_opener.reveal(FILE)
    with pytest.raises(folder_opener.OpenerUnavailable):
        folder_opener.open_folder(FOLDER)
