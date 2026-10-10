"""Where the pieces of an installed Maestro live: Node, the web bundle, the launcher."""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent


def node_path(platform: str = sys.platform, environ: Mapping[str, str] = os.environ) -> Path:
    """The Node binary that ships in the nodejs-wheel-binaries dependency (MAESTRO_NODE wins)."""
    override = (environ.get("MAESTRO_NODE") or "").strip()
    if override:
        return Path(override)
    spec = importlib.util.find_spec("nodejs_wheel")
    root = Path(spec.origin).parent if spec and spec.origin else Path("nodejs_wheel")
    return root / "node.exe" if platform == "win32" else root / "bin" / "node"


def web_dir(environ: Mapping[str, str] = os.environ) -> Path:
    """The Next standalone bundle (MAESTRO_WEB_DIR wins)."""
    override = (environ.get("MAESTRO_WEB_DIR") or "").strip()
    return Path(override) if override else _PACKAGE / "web"


def web_dir_ok(environ: Mapping[str, str] = os.environ) -> bool:
    return (web_dir(environ) / "server.js").is_file()


def demo_resume() -> Path | None:
    """The example resume: bundled in the wheel, else the checkout's copy for source runs."""
    for candidate in (_PACKAGE / "data" / "example.json",
                      _PACKAGE.parents[1] / "base_resumes" / "example.json"):
        if candidate.is_file():
            return candidate
    return None


def _inside(path: Path, prefix: Path) -> bool:
    try:
        path.resolve().relative_to(prefix.resolve())
    except ValueError:
        return False
    return True


def shim_path(platform: str = sys.platform, which: Callable[[str], str | None] = shutil.which,
              prefix: str = sys.prefix) -> Path:
    """Absolute path of this environment's `maestro` launcher.

    GUI apps (Claude Desktop) and autostart units have no `~/.local/bin` on PATH, so callers
    that write config use this path, never the bare word `maestro`.
    """
    venv = Path(prefix)
    found = which("maestro")
    if found and _inside(Path(found), venv):
        return Path(os.path.abspath(found))
    if platform == "win32":
        return venv / "Scripts" / "maestro.exe"
    return venv / "bin" / "maestro"


def venv_python(platform: str = sys.platform, windowless: bool = False,
                executable: str = sys.executable) -> Path:
    """This environment's interpreter; `pythonw.exe` on Windows when no console is wanted."""
    python = Path(executable)
    if platform == "win32" and windowless:
        return python.with_name("pythonw.exe")
    return python
