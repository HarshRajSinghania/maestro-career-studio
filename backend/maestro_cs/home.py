"""Where a uv install keeps its data, and the environment its two servers run with.

The signed desktop app (branch C) uses the same folder, so it inherits this data with no import.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

APP_DIR_NAME = "Maestro Career Studio"
LAYOUT = (
    "data", "applications", "settings", "base_resumes", "kb_documents", "logs", "exports",
    "fastembed_cache", "uploads", "backups", "run",
)
DEFAULT_BACKEND_PORT = 8001
DEFAULT_WEB_PORT = 3000
TEMPLATE = Path(__file__).with_name("maestro.env.template")
POSIX = os.name == "posix"

# Never inherited by the backend: a stray value would point it at someone else's data.
_STRIPPED = (
    "DATABASE_URL", "TEST_DATABASE_URL", "MAESTRO_CS_UPLOAD_HOST_ROOT", "MCPB_BUNDLE_PATH",
    "APP_GIT_SHA",
)

# Never given to the web server, even when the user's shell exports them: it needs no secret.
# Covers every secret-bearing field of app.config.Settings (a test walks the model).
_WEB_STRIPPED = _STRIPPED + (
    "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL", "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "SYNC_REMOTE_URL", "SYNC_KEY_FILE",
)


class HomeError(Exception):
    """A problem with the install folder, phrased for the person at the terminal."""


@dataclass(frozen=True)
class Ports:
    backend: int = DEFAULT_BACKEND_PORT
    web: int = DEFAULT_WEB_PORT


def default_home(platform: str = sys.platform, environ: Mapping[str, str] = os.environ,
                 user_home: Path | None = None) -> Path:
    user_home = user_home or Path.home()
    if platform == "darwin":
        return user_home / "Library" / "Application Support" / APP_DIR_NAME
    if platform == "win32":
        return Path(environ.get("APPDATA") or user_home / "AppData" / "Roaming") / APP_DIR_NAME
    return Path(environ.get("XDG_DATA_HOME") or user_home / ".local" / "share") / "maestro-career-studio"


def resolve_home(environ: Mapping[str, str] = os.environ) -> Path:
    raw = environ.get("MAESTRO_HOME", "").strip()
    home = Path(raw).expanduser() if raw else default_home(environ=environ)
    return Path(os.path.abspath(home))


def prepare_home(home: Path) -> None:
    if home.is_symlink():
        raise HomeError(f"{home} is a symlink. Point MAESTRO_HOME at a real folder.")
    try:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if POSIX:
            home.chmod(0o700)
        for name in LAYOUT:
            (home / name).mkdir(exist_ok=True, mode=0o700)
        env_file = home / "maestro.env"
        if env_file.is_symlink():
            raise HomeError(f"{env_file} is a symlink. Replace it with a regular file.")
        if not env_file.exists():
            fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(TEMPLATE.read_text(encoding="utf-8"))
        elif POSIX:
            env_file.chmod(0o600)
    except OSError as exc:
        raise HomeError(f"Cannot set up {home}: {exc.strerror or exc}.") from None


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines; a blank value means unset (SYSTEM.md's scrub rule)."""
    values = dotenv_values(path) if path.is_file() else {}
    return {k: v.strip() for k, v in values.items() if v is not None and v.strip()}


def _port(value: str | None, default: int, name: str) -> int:
    if value is None:
        return default
    if not value.isdigit() or not 0 < int(value) <= 65535:
        raise HomeError(f"{name} must be a port number from 1 to 65535.")
    return int(value)


def ports_from(settings: Mapping[str, str], environ: Mapping[str, str] = os.environ) -> Ports:
    """Process environment beats maestro.env, which beats the default."""
    def pick(name: str) -> str | None:
        return (environ.get(name) or "").strip() or settings.get(name)

    ports = Ports(
        _port(pick("MAESTRO_PORT"), DEFAULT_BACKEND_PORT, "MAESTRO_PORT"),
        _port(pick("MAESTRO_WEB_PORT"), DEFAULT_WEB_PORT, "MAESTRO_WEB_PORT"),
    )
    if ports.backend == ports.web:
        raise HomeError("MAESTRO_PORT and MAESTRO_WEB_PORT must differ.")
    return ports


def backend_env(home: Path, settings: Mapping[str, str], environ: Mapping[str, str],
                ports: Ports, version: str) -> dict[str, str]:
    env = {k: v for k, v in environ.items() if k not in _STRIPPED}
    env.update({k: v for k, v in settings.items() if k not in _STRIPPED})
    env.setdefault(
        "ALLOWED_WEB_ORIGINS", f"http://localhost:{ports.web},http://127.0.0.1:{ports.web}"
    )
    env.update({
        "MAESTRO_HOME": str(home),
        "DATA_DIR": str(home / "data"),
        "APPLICATIONS_DIR": str(home / "applications"),
        "SETTINGS_DIR": str(home / "settings"),
        "BASE_RESUMES_DIR": str(home / "base_resumes"),
        "KB_DOCUMENTS_DIR": str(home / "kb_documents"),
        "LOGS_DIR": str(home / "logs"),
        "EXPORTS_DIR": str(home / "exports"),
        "FASTEMBED_CACHE_PATH": str(home / "fastembed_cache"),
        "MAESTRO_CS_UPLOAD_DIR": str(home / "uploads"),
        # Sync is on only when this file exists; the path is never printed.
        "SYNC_KEY_FILE": str(home / "sync-key"),
        "ALLOWED_HOSTS": "localhost,127.0.0.1",
        "BACKEND_URL": f"http://127.0.0.1:{ports.backend}",
        "MAESTRO_CS_PUBLIC_BACKEND_URL": f"http://localhost:{ports.backend}",
        "MAESTRO_INSTALL": "uv",
        "APP_VERSION": version,
        "PYTHONUNBUFFERED": "1",
    })
    return env


def web_env(settings: Mapping[str, str], environ: Mapping[str, str], ports: Ports) -> dict[str, str]:
    # Built from the parent environment (Node needs PATH, SYSTEMROOT, proxy variables), never
    # from maestro.env, and minus every secret the backend reads.
    env = {k: v for k, v in environ.items() if k not in _WEB_STRIPPED and k not in settings}
    if settings.get("FRONTEND_ALLOWED_HOSTS"):
        env["FRONTEND_ALLOWED_HOSTS"] = settings["FRONTEND_ALLOWED_HOSTS"]
    env.update({
        "PORT": str(ports.web),
        "HOSTNAME": "127.0.0.1",
        "API_PROXY_BACKEND": f"http://127.0.0.1:{ports.backend}",
        "NODE_ENV": "production",
        "NEXT_TELEMETRY_DISABLED": "1",
    })
    return env


def apply_env(home: Path, env: Mapping[str, str]) -> None:
    """Make this process look like the backend child, for one-shot verbs.

    Call it before importing anything under `app`: `app.config.settings` reads the environment
    at import, and `Settings(env_file=".env")` would otherwise read a `.env` from the caller's
    folder. Moving into the home folder makes that file impossible to find.
    """
    for name in _STRIPPED:
        os.environ.pop(name, None)
    os.environ.update(env)
    os.chdir(home)
