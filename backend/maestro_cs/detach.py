"""Start `python -m maestro_cs <verb> ...` as a child that outlives this process.

Used by `maestro connect` (the Claude Desktop waiter) and reusable by `maestro update`'s Windows
handoff. The child's output goes to a log file, never the terminal: it keeps running after the
terminal closes. POSIX children get their own session so a closed terminal's SIGHUP misses them;
Windows children get their own process group and no console window.
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from maestro_cs import runtime

# subprocess.CREATE_* exist only on Windows; the values are fixed by the Win32 API.
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


class DetachError(Exception):
    """The child could not be started, phrased for the person at the terminal."""


def spawn_detached(
    args: list[str],
    *,
    log: Path,
    home: Path,
    env: Mapping[str, str] | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
    platform: str = sys.platform,
    executable: str = sys.executable,
) -> Any:
    """Run this environment's interpreter with `-m maestro_cs <args>`, detached, appending to `log`.

    Always the console interpreter (`python.exe`, not `pythonw.exe`), as the supervisor does: the
    flags above already keep a window from appearing. `MAESTRO_HOME` is pinned so the child finds
    the same install folder this process resolved.
    """
    argv = [str(runtime.venv_python(platform, windowless=False, executable=executable)),
            "-m", "maestro_cs", *args]
    child_env = {**(os.environ if env is None else env), "MAESTRO_HOME": str(home)}
    kwargs: dict[str, Any] = (
        {"creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}
        if platform == "win32" else {"start_new_session": True}
    )
    try:
        handle = open(log, "ab")  # noqa: SIM115  (closed below; the child keeps its own copy)
    except OSError as exc:
        raise DetachError(f"Couldn't open {log}: {exc.strerror or exc}.") from None
    with handle:
        try:
            return popen(argv, cwd=home, env=child_env, stdin=subprocess.DEVNULL,
                         stdout=handle, stderr=subprocess.STDOUT, **kwargs)
        except OSError as exc:
            raise DetachError(
                f"Couldn't start a background step: {argv[0]} could not be run "
                f"({exc.strerror or exc})."
            ) from None
