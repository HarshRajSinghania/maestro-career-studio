import os
import stat
import sys
from pathlib import Path

import pytest

from maestro_cs import home as H
from maestro_cs.home import HomeError, Ports

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def test_default_home_per_platform():
    assert H.default_home("darwin", {}, Path("/Users/u")) == Path(
        "/Users/u/Library/Application Support/Maestro Career Studio"
    )
    win = H.default_home("win32", {"APPDATA": r"C:\Users\u\AppData\Roaming"}, Path("/Users/u"))
    assert win.name == "Maestro Career Studio"
    assert str(win).startswith(r"C:\Users\u\AppData\Roaming")
    assert H.default_home("linux", {}, Path("/home/u")) == Path(
        "/home/u/.local/share/maestro-career-studio"
    )
    assert H.default_home("linux", {"XDG_DATA_HOME": "/x"}, Path("/home/u")) == Path(
        "/x/maestro-career-studio"
    )


def test_resolve_home_env_wins_expands_and_absolutises(tmp_path, monkeypatch):
    assert H.resolve_home({"MAESTRO_HOME": str(tmp_path)}) == tmp_path
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert H.resolve_home({"MAESTRO_HOME": "~/m"}) == tmp_path / "m"
    monkeypatch.chdir(tmp_path)
    assert H.resolve_home({"MAESTRO_HOME": "rel/m"}) == tmp_path / "rel" / "m"
    assert H.resolve_home({"MAESTRO_HOME": "  "}).name  # blank falls back to the default


def test_prepare_home_creates_layout_and_env_file(tmp_path):
    home = tmp_path / "h"
    H.prepare_home(home)
    for name in H.LAYOUT:
        assert (home / name).is_dir()
    env_file = home / "maestro.env"
    assert env_file.read_text(encoding="utf-8") == H.TEMPLATE.read_text(encoding="utf-8")
    if sys.platform != "win32":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(home.stat().st_mode) == 0o700


def test_prepare_home_keeps_an_existing_env_file(tmp_path):
    H.prepare_home(tmp_path)
    (tmp_path / "maestro.env").write_bytes(b"OPENAI_API_KEY=abc\r\n# mine\n")
    H.prepare_home(tmp_path)
    assert (tmp_path / "maestro.env").read_bytes() == b"OPENAI_API_KEY=abc\r\n# mine\n"


@posix_only
def test_prepare_home_tightens_loose_modes(tmp_path):
    H.prepare_home(tmp_path)
    os.chmod(tmp_path, 0o755)
    os.chmod(tmp_path / "maestro.env", 0o644)
    H.prepare_home(tmp_path)
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "maestro.env").stat().st_mode) == 0o600


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_prepare_home_refuses_symlinks(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted")
    with pytest.raises(HomeError, match="symlink"):
        H.prepare_home(link)

    home = tmp_path / "h"
    H.prepare_home(home)
    (home / "maestro.env").unlink()
    (home / "maestro.env").symlink_to(real / "elsewhere.env")
    with pytest.raises(HomeError, match="symlink"):
        H.prepare_home(home)


def test_read_env_file_syntax(tmp_path):
    path = tmp_path / "maestro.env"
    path.write_text(
        "# comment\n"
        "A=plain\n"
        "B='v w'\n"
        'C="quoted"\n'
        "export D=exported\n"
        "E=\n"
        "F='  '\n"
        "\n"
        "  G = spaced  \n",
        encoding="utf-8",
    )
    assert H.read_env_file(path) == {
        "A": "plain", "B": "v w", "C": "quoted", "D": "exported", "G": "spaced",
    }
    assert H.read_env_file(tmp_path / "missing.env") == {}


def test_ports_defaults_and_precedence():
    assert H.ports_from({}, {}) == Ports(8001, 3000)
    assert H.ports_from({"MAESTRO_PORT": "9001"}, {}) == Ports(9001, 3000)
    # process env beats maestro.env, which beats the default
    got = H.ports_from(
        {"MAESTRO_PORT": "9001", "MAESTRO_WEB_PORT": "9002"}, {"MAESTRO_PORT": "18001"}
    )
    assert got == Ports(18001, 9002)
    # a blank process variable does not shadow the file
    assert H.ports_from({"MAESTRO_PORT": "9001"}, {"MAESTRO_PORT": " "}).backend == 9001


@pytest.mark.parametrize("bad", ["0", "70000", "abc", "-1", "80.5"])
def test_ports_reject_bad_values(bad):
    with pytest.raises(HomeError, match="MAESTRO_PORT"):
        H.ports_from({}, {"MAESTRO_PORT": bad})
    with pytest.raises(HomeError, match="MAESTRO_WEB_PORT"):
        H.ports_from({"MAESTRO_WEB_PORT": bad}, {})


def test_ports_must_differ():
    with pytest.raises(HomeError, match="differ"):
        H.ports_from({}, {"MAESTRO_PORT": "3000"})


def _backend(tmp_path, settings=None, environ=None, ports=Ports(8001, 3000)):
    return H.backend_env(tmp_path, settings or {}, environ or {}, ports, "1.2.3")


def test_backend_env_paths_and_enforced_values(tmp_path):
    env = _backend(tmp_path, ports=Ports(18001, 18000))
    assert env["MAESTRO_HOME"] == str(tmp_path)
    for key, folder in {
        "DATA_DIR": "data", "APPLICATIONS_DIR": "applications", "SETTINGS_DIR": "settings",
        "BASE_RESUMES_DIR": "base_resumes", "KB_DOCUMENTS_DIR": "kb_documents",
        "LOGS_DIR": "logs", "EXPORTS_DIR": "exports", "FASTEMBED_CACHE_PATH": "fastembed_cache",
        "MAESTRO_CS_UPLOAD_DIR": "uploads",
    }.items():
        assert env[key] == str(tmp_path / folder)
    assert env["SYNC_KEY_FILE"] == str(tmp_path / "sync-key")
    assert env["MAESTRO_INSTALL"] == "uv"
    assert env["APP_VERSION"] == "1.2.3"
    assert env["BACKEND_URL"] == "http://127.0.0.1:18001"
    assert env["MAESTRO_CS_PUBLIC_BACKEND_URL"] == "http://localhost:18001"
    assert env["ALLOWED_HOSTS"] == "localhost,127.0.0.1"
    assert env["ALLOWED_WEB_ORIGINS"] == "http://localhost:18000,http://127.0.0.1:18000"


def test_backend_env_strips_foreign_data_pointers(tmp_path):
    poison = {k: "x" for k in H._STRIPPED}
    poison.update(DATABASE_URL="sqlite:////other.db", MCPB_BUNDLE_PATH="/b.mcpb")
    for env in (_backend(tmp_path, environ=poison), _backend(tmp_path, settings=poison)):
        for name in ("DATABASE_URL", "TEST_DATABASE_URL", "MAESTRO_CS_UPLOAD_HOST_ROOT",
                     "MCPB_BUNDLE_PATH"):
            assert name not in env


def test_backend_env_precedence(tmp_path):
    env = _backend(
        tmp_path,
        settings={"FAST_MODEL": "from-file", "DATA_DIR": "/elsewhere", "ALLOWED_WEB_ORIGINS": "http://a"},
        environ={"FAST_MODEL": "from-shell", "SMART_MODEL": "shell-only", "DATA_DIR": "/shell"},
    )
    assert env["FAST_MODEL"] == "from-file"      # file beats inherited
    assert env["SMART_MODEL"] == "shell-only"    # inherited passes through
    assert env["DATA_DIR"] == str(tmp_path / "data")  # enforced beats both
    assert env["ALLOWED_WEB_ORIGINS"] == "http://a"   # the file may set the origins


def test_web_env_shape(tmp_path):
    env = H.web_env({}, {"PATH": "/bin"}, Ports(18001, 18000))
    assert env["PORT"] == "18000"
    assert env["HOSTNAME"] == "127.0.0.1"
    assert env["API_PROXY_BACKEND"] == "http://127.0.0.1:18001"
    assert env["NODE_ENV"] == "production"
    assert env["NEXT_TELEMETRY_DISABLED"] == "1"
    assert env["PATH"] == "/bin"


def test_web_env_passes_only_frontend_allowed_hosts_from_the_file():
    settings = {
        "FRONTEND_ALLOWED_HOSTS": "me.local", "OPENAI_API_KEY": "sk-file", "FAST_MODEL": "m",
        "HTTPS_PROXY": "http://p",
    }
    env = H.web_env(settings, {"PATH": "/bin"}, Ports())
    assert env["FRONTEND_ALLOWED_HOSTS"] == "me.local"
    for key in settings:
        if key != "FRONTEND_ALLOWED_HOSTS":
            assert key not in env, key


def test_web_env_strips_secrets_inherited_from_the_shell():
    shell = {name: "secret" for name in H._WEB_STRIPPED}
    shell.update(PATH="/bin", SYSTEMROOT="C:\\Windows")
    env = H.web_env({}, shell, Ports())
    for name in H._WEB_STRIPPED:
        assert name not in env, name
    assert env["PATH"] == "/bin" and env["SYSTEMROOT"] == "C:\\Windows"


def test_web_stripped_covers_every_secret_field_of_settings():
    from app.config import Settings

    secret_like = [
        name.upper() for name in Settings.model_fields
        if any(word in name for word in ("api_key", "secret", "token", "password", "key_file"))
    ]
    assert secret_like  # the heuristic still finds the known ones
    for name in secret_like:
        assert name in H._WEB_STRIPPED, name
    # and the remote sync address, which names a private host
    assert "SYNC_REMOTE_URL" in H._WEB_STRIPPED


def test_web_env_never_receives_a_key_from_the_env_file_even_if_also_exported(tmp_path):
    settings = {"OPENAI_API_KEY": "sk-file", "CUSTOM": "1"}
    env = H.web_env(settings, {"OPENAI_API_KEY": "sk-shell", "CUSTOM": "2"}, Ports())
    assert "OPENAI_API_KEY" not in env and "CUSTOM" not in env


def test_apply_env_sets_strips_and_chdirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.setenv("DATABASE_URL", "sqlite:////other.db")
    monkeypatch.setenv("KEEP_ME", "1")
    monkeypatch.setenv("STALE_FROM_SHELL", "1")
    backend = {"DATA_DIR": str(tmp_path / "data"), "MAESTRO_INSTALL": "uv", "KEEP_ME": "2"}
    H.apply_env(tmp_path, backend)
    assert os.environ["DATA_DIR"] == str(tmp_path / "data")
    assert os.environ["MAESTRO_INSTALL"] == "uv"
    assert os.environ["KEEP_ME"] == "2"
    assert os.environ["STALE_FROM_SHELL"] == "1"
    assert "DATABASE_URL" not in os.environ
    assert Path.cwd().resolve() == tmp_path.resolve()
