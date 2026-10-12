"""Open a folder (or select a file) in the OS file manager, on the machine the user sits at.

Callers pass only paths Maestro computed. Nothing here accepts a path from a request.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


class OpenerUnavailable(Exception):
    """The OS file manager could not be started."""


def command(target: Path, *, select: bool, platform: str = sys.platform) -> list[str] | str:
    if platform == "darwin":
        return ["open", "-R", str(target)] if select else ["open", str(target)]
    if platform == "win32":
        # A string, not a list: list2cmdline would quote "/select,<path>" whole and Explorer ignores it.
        return f'explorer /select,"{target}"' if select else f'explorer "{target}"'
    return ["xdg-open", str(target.parent if select else target)]  # xdg-open cannot select a file


def _launch(cmd: list[str] | str) -> None:
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)  # noqa: S603 — fixed program, computed path, no shell
    except OSError as exc:
        raise OpenerUnavailable(str(cmd)) from exc


def reveal(file: Path) -> None:
    _launch(command(file, select=True))


def open_folder(folder: Path) -> None:
    _launch(command(folder, select=False))
