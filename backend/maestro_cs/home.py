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
    "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
    "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "SYNC_REMOTE_URL", "SYNC_KEY_FILE",
    # frontend/lib/api.ts reads INTERNAL_API_URL before API_PROXY_BACKEND; only ours may apply.
    "INTERNAL_API_URL", "BACKEND_URL",
)


_ORIGINS = "ALLOWED_WEB_ORIGINS"


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
            template = TEMPLATE.read_text(encoding="utf-8")  # before O_EXCL: no empty file on failure
            fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(template)
        elif POSIX:
            env_file.chmod(0o600)
    except OSError as exc:
        raise HomeError(f"Cannot set up {home}: {exc.strerror or exc}.") from None


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines; a blank value means unset (SYSTEM.md's scrub rule)."""
    # interpolate=False: `${HOME}/x` and `pa$$word` are the user's literal values.
    try:
        values = dotenv_values(path, interpolate=False) if path.is_file() else {}
    except OSError as exc:
        raise HomeError(f"Cannot read {path}: {exc.strerror or exc}.") from None
    except UnicodeDecodeError:
        raise HomeError(f"Cannot read {path}: it is not UTF-8 text. Save it as UTF-8.") from None
    return {k: v.strip() for k, v in values.items() if v is not None and v.strip()}


def _port(value: str | None, default: int, name: str) -> int:
    if value is None:
        return default
    if not (value.isascii() and value.isdigit()) or not 0 < int(value) <= 65535:
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


def _without(values: Mapping[str, str], upper_names: set[str]) -> dict[str, str]:
    """Drop names case-insensitively: pydantic-settings reads `database_url` as DATABASE_URL."""
    return {k: v for k, v in values.items() if k.upper() not in upper_names}


def child_env(environ: Mapping[str, str], *, platform: str | None = None) -> dict[str, str]:
    """Keep Windows loader variables even when a caller supplies a minimal environment."""
    env = dict(environ)
    if (platform or sys.platform) == "win32":
        present = {name.upper() for name in env}
        for name, value in os.environ.items():
            upper = name.upper()
            if upper in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"} and upper not in present:
                env[upper] = value
                present.add(upper)
    return env


def backend_env(home: Path, settings: Mapping[str, str], environ: Mapping[str, str],
                ports: Ports, version: str) -> dict[str, str]:
    enforced = {
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
    }
    # The origins come from maestro.env or the default, never from the inherited shell.
    origins = next((v for k, v in settings.items() if k.upper() == _ORIGINS), None) or (
        f"http://localhost:{ports.web},http://127.0.0.1:{ports.web}"
    )
    blocked = set(_STRIPPED) | set(enforced) | {_ORIGINS}
    # A name set in maestro.env wins over the shell in any capitalisation.
    env = _without(environ, blocked | {k.upper() for k in settings})
    env.update(_without(settings, blocked))
    env[_ORIGINS] = origins
    env.update(enforced)
    return child_env(env)


def web_env(settings: Mapping[str, str], environ: Mapping[str, str], ports: Ports) -> dict[str, str]:
    # Built from the parent environment (Node needs PATH, SYSTEMROOT, proxy variables), never
    # from maestro.env, and minus every secret the backend reads.
    enforced = {
        "PORT": str(ports.web),
        "HOSTNAME": "127.0.0.1",
        "API_PROXY_BACKEND": f"http://127.0.0.1:{ports.backend}",
        "NODE_ENV": "production",
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    blocked = (
        set(_WEB_STRIPPED) | set(enforced) | {k.upper() for k in settings} | {"FRONTEND_ALLOWED_HOSTS"}
    )
    env = _without(environ, blocked)
    hosts = next((v for k, v in settings.items() if k.upper() == "FRONTEND_ALLOWED_HOSTS"), None)
    if hosts:
        env["FRONTEND_ALLOWED_HOSTS"] = hosts
    env.update(enforced)
    return child_env(env)


def apply_env(home: Path, env: Mapping[str, str]) -> None:
    """Make this process look like the backend child, for one-shot verbs.

    Call it before importing anything under `app`: `app.config.settings` reads the environment
    at import, and `Settings(env_file=".env")` would otherwise read a `.env` from the caller's
    folder. Moving into the home folder makes that file impossible to find.
    """
    wanted = {k.upper() for k in env}
    for name in list(os.environ):
        if name.upper() in _STRIPPED or (name.upper() in wanted and name not in env):
            del os.environ[name]
    os.environ.update(env)
    os.chdir(home)
