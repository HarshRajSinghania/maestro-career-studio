"""A native "choose a folder" dialog, on the machine the user sits at.

The start path is one Maestro computed (the saved folder or the home folder), never one from a request.
Nothing here uses a shell. The dialog blocks until the user answers, so callers run it off the event loop.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

TIMEOUT_SECONDS = 300
PROMPT = "Choose where Maestro saves your resumes"
START_ENV = "MAESTRO_PICKER_START"

_WINDOWS_SCRIPT = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
    f"$d.Description = '{PROMPT}'; "
    f"if ($env:{START_ENV}) {{ $d.SelectedPath = $env:{START_ENV} }}; "
    "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($d.SelectedPath) }"
)

_lock = threading.Lock()


class PickerBusy(Exception):
    """A folder dialog is already open."""


class PickerFailed(Exception):
    """The dialog could not be shown, timed out or exited with an error."""


def available(platform: str = sys.platform) -> bool:
    if platform == "darwin":
        return shutil.which("osascript") is not None
    if platform == "win32":
        return shutil.which("powershell") is not None
    return shutil.which("zenity") is not None


def is_dir(path: Path) -> bool:
    """Path.is_dir() raises PermissionError on Python 3.12 for some inaccessible folders; that is just "no"."""
    try:
        return path.is_dir()
    except OSError:
        return False


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _command(start: Path, platform: str) -> tuple[list[str], dict[str, str] | None]:
    """The argv for the platform, and an env override (Windows only) or None."""
    exists = is_dir(start)
    if platform == "darwin":
        location = f" default location (POSIX file {_applescript_string(str(start))})" if exists else ""
        script = f"POSIX path of (choose folder with prompt {_applescript_string(PROMPT)}{location})"
        return ["osascript", "-e", script], None
    if platform == "win32":
        # The start path travels in the environment so it is never part of the script text.
        env = {**os.environ, START_ENV: str(start) if exists else ""}
        return ["powershell", "-NoProfile", "-STA", "-Command", _WINDOWS_SCRIPT], env
    cmd = ["zenity", "--file-selection", "--directory", f"--title={PROMPT}"]
    if exists:
        cmd.append(f"--filename={start}/")
    return cmd, None


def _is_cancel(platform: str, returncode: int, stderr: str) -> bool:
    if platform == "darwin":
        return returncode == 1 and "-128" in (stderr or "")
    if platform == "win32":
        return False  # a cancel is a clean exit with no output
    return returncode == 1


def choose(start: Path, platform: str = sys.platform) -> Path | None:
    """The folder the user picked, or None when they cancelled."""
    if not _lock.acquire(blocking=False):
        raise PickerBusy
    try:
        cmd, env = _command(start, platform)
        try:
            done = subprocess.run(  # noqa: S603 — fixed program, computed path, no shell
                cmd, capture_output=True, encoding="utf-8", timeout=TIMEOUT_SECONDS, env=env, check=False,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:  # TimeoutExpired; UnicodeDecodeError is a ValueError
            raise PickerFailed(type(exc).__name__) from exc
        if _is_cancel(platform, done.returncode, done.stderr):
            return None
        if done.returncode != 0:
            raise PickerFailed(f"exit {done.returncode}")
        text = (done.stdout or "").strip().lstrip("\ufeff").strip()  # a BOM from the shell is not part of the path
        return Path(text) if text else None  # Path drops the trailing separator macOS adds
    finally:
        _lock.release()
