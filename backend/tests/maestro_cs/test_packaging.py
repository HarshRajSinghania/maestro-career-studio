import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _project():
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_distribution_name_and_command():
    data = _project()
    assert data["project"]["name"] == "maestro-career-studio"
    assert data["project"]["scripts"]["maestro"] == "maestro_cs.cli:main"
    # The legacy stdio entry point stays for one release (SYSTEM.md §13 mcp-stdio-venv).
    assert data["project"]["scripts"]["maestro-career-studio-mcp"] == "mcp_server.server:main"


def test_runtime_dependencies_for_the_uv_install():
    deps = " ".join(_project()["project"]["dependencies"])
    for name in ("psutil", "python-dotenv", "nodejs-wheel-binaries"):
        assert name in deps


def test_wheel_ships_migrations_and_the_cli_package():
    find = _project()["tool"]["setuptools"]["packages"]["find"]
    assert {"migrations*", "maestro_cs*"} <= set(find["include"])
    assert not any(p.startswith("migrations") for p in find["exclude"])
    data = _project()["tool"]["setuptools"]["package-data"]
    assert {"script.py.mako", "prompt_defaults.lock.json"} <= set(data["migrations"])
    assert "web/**/*" in data["maestro_cs"]


def test_pypi_metadata():
    project = _project()["project"]
    assert project["readme"] == "PYPI.md"
    assert project["requires-python"] == ">=3.12"
    assert "Homepage" in project["urls"]
