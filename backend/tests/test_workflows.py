"""The CI and PyPI release workflows keep the shape the uv install depends on.

GitHub reads these files only after a push, so nothing else would notice a dropped matrix OS, an
unpinned action, a publish job that no longer waits for the smoke, or a token creeping into the
repo. Parsed, never run. Also pins the flags the workflows pass to scripts/ against those scripts'
own `--help`, and the pure helpers of scripts/upgrade_check.py.
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / "scripts"
ALL_OS = ["macos-latest", "ubuntu-latest", "windows-latest"]
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    return doc.get("on") or doc[True]  # PyYAML (YAML 1.1) reads the key `on` as True


def _steps(job: dict) -> list[dict]:
    return job["steps"]


def _runs(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in _steps(job))


def _step_using(job: dict, action: str) -> dict:
    found = [s for s in _steps(job) if str(s.get("uses", "")).startswith(action + "@")]
    assert len(found) == 1, f"expected one {action} step, found {len(found)}"
    return found[0]


def _load_upgrade_check():
    spec = importlib.util.spec_from_file_location("upgrade_check", SCRIPTS / "upgrade_check.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["upgrade_check"] = module
    spec.loader.exec_module(module)
    return module


upgrade_check = _load_upgrade_check()


def _model_id() -> str:
    text = (ROOT / "backend" / "app" / "services" / "ats" / "data" / "weights.yaml").read_text(encoding="utf-8")
    return re.search(r"^\s*model:\s*(\S+)", text, re.M).group(1)


@pytest.mark.parametrize("name", ["ci.yml", "pypi.yml", "images.yml", "codeql.yml"])
def test_every_action_is_pinned_to_a_commit_sha(name):
    doc = _workflow(name)
    for job_name, job in doc["jobs"].items():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses:
                assert PINNED.match(uses), f"{name} {job_name}: {uses} is not pinned to a full commit SHA"
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    for line in text.splitlines():
        if "uses:" in line and "@" in line:
            assert re.search(r"#\s*v\d", line), f"{name}: pinned action without its version comment: {line.strip()}"


# --- ci.yml -----------------------------------------------------------------------------------

def test_ci_triggers_have_no_path_filters():
    triggers = _on(_workflow("ci.yml"))
    assert set(triggers) == {"push", "pull_request"}
    assert triggers["push"] == {"branches": ["main"]}
    assert triggers["pull_request"] == {"branches": ["main"]}


def test_maestro_cli_unit_job_runs_on_all_three_oses():
    job = _workflow("ci.yml")["jobs"]["maestro-cli"]
    assert job["strategy"]["matrix"]["os"] == ["ubuntu-latest", "macos-latest", "windows-latest"]
    assert job["strategy"]["fail-fast"] is False
    assert _step_using(job, "actions/setup-python")["with"]["python-version"] == "3.12"
    runs = _runs(job)
    assert 'pip install -e "backend[dev]"' in runs
    assert "pytest tests/maestro_cs -q" in runs


def test_wheel_job_builds_dev1_with_node_24_python_312_and_uv():
    job = _workflow("ci.yml")["jobs"]["uv-wheel"]
    assert job["runs-on"] == "ubuntu-latest"
    assert _step_using(job, "actions/setup-node")["with"]["node-version"] == "24"
    assert _step_using(job, "actions/setup-python")["with"]["python-version"] == "3.12"
    _step_using(job, "astral-sh/setup-uv")
    assert "scripts/build_wheel.py --version 0.0.0.dev1" in _runs(job)
    upload = _step_using(job, "actions/upload-artifact")
    assert upload["with"]["path"] == "dist/*.whl"
    assert upload["with"]["if-no-files-found"] == "error"


def test_smoke_job_installs_the_built_wheel_on_every_os_with_a_model_cache():
    ci = _workflow("ci.yml")
    job = ci["jobs"]["uv-smoke"]
    assert job["needs"] == "uv-wheel"
    assert sorted(job["strategy"]["matrix"]["os"]) == ALL_OS
    assert job["strategy"]["fail-fast"] is False
    assert _step_using(job, "actions/download-artifact")["with"]["name"] == "uv-wheel"
    cache = _step_using(job, "actions/cache")["with"]
    assert cache["path"] == "~/.cache/maestro-smoke-model"
    assert _model_id().replace("/", "-") in cache["key"]
    seeds = [s for s in _steps(job) if "FASTEMBED_CACHE_PATH" in s.get("run", "")]
    assert seeds and seeds[0]["shell"] == "python"
    assert "python scripts/smoke_uv_install.py --wheel dist/*.whl --expect-version 0.0.0.dev1" in _runs(job)
    assert job["defaults"]["run"]["shell"] == "bash"  # the wheel glob must expand on Windows too


def test_upgrade_job_updates_dev1_to_dev2_on_ubuntu_and_windows():
    job = _workflow("ci.yml")["jobs"]["uv-upgrade"]
    assert job["needs"] == "uv-wheel"
    assert job["strategy"]["matrix"]["os"] == ["ubuntu-latest", "windows-latest"]
    runs = _runs(job)
    assert "scripts/build_wheel.py --version 0.0.0.dev2 --web-from dist1/*.whl" in runs
    assert "scripts/upgrade_check.py" in runs
    assert "--old-version 0.0.0.dev1" in runs and "--new-version 0.0.0.dev2" in runs
    assert "--wait 120" in runs
    cache = _step_using(job, "actions/cache/restore")["with"]  # restore only: the smoke job saves it
    assert cache["key"] == _step_using(_workflow("ci.yml")["jobs"]["uv-smoke"], "actions/cache")["with"]["key"]


def test_existing_ci_jobs_are_still_there():
    assert {"backend", "mcp-cold-install", "shellcheck", "update-check", "docs-gate", "frontend",
            "dependency-audit"} <= set(_workflow("ci.yml")["jobs"])


# --- pypi.yml ---------------------------------------------------------------------------------

def test_pypi_workflow_triggers_on_version_tags_and_by_hand():
    triggers = _on(_workflow("pypi.yml"))
    assert triggers["push"] == {"tags": ["v*"]}
    assert "workflow_dispatch" in triggers


def test_pypi_build_checks_the_tag_against_the_project_version():
    job = _workflow("pypi.yml")["jobs"]["build"]
    assert job["runs-on"] == "ubuntu-24.04"
    check = next(s for s in _steps(job) if "does not match" in s.get("run", ""))
    assert check["if"] == "github.ref_type == 'tag'"
    assert '"${TAG#v}" != "$VERSION"' in check["run"]
    assert check["env"]["TAG"] == "${{ github.ref_name }}"  # never interpolated into the script
    assert "backend/pyproject.toml" in _runs(job)
    runs = _runs(job)
    assert "scripts/build_wheel.py" in runs and "scripts/check_wheel.py" in runs
    assert job["outputs"]["version"]


def test_pypi_smoke_runs_on_all_three_oses_before_any_publish():
    jobs = _workflow("pypi.yml")["jobs"]
    smoke = jobs["smoke"]
    assert smoke["needs"] == "build"
    assert sorted(smoke["strategy"]["matrix"]["os"]) == ALL_OS
    assert "scripts/smoke_uv_install.py --wheel dist/*.whl --expect-version" in _runs(smoke)


def test_pypi_publish_is_tag_only_trusted_and_token_free():
    doc = _workflow("pypi.yml")
    jobs = doc["jobs"]
    publish = jobs["publish"]
    assert publish["needs"] == "smoke"
    assert publish["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert publish["environment"]["name"] == "pypi"
    assert publish["permissions"] == {"id-token": "write"}
    assert doc["permissions"] == {"contents": "read"}
    others = {name: job for name, job in jobs.items() if name != "publish"}
    for name, job in others.items():
        assert "id-token" not in (job.get("permissions") or {}), f"{name} must not mint an OIDC token"
        assert "pypa/gh-action-pypi-publish" not in str(job), f"{name} publishes; only `publish` may"
    step = _step_using(publish, "pypa/gh-action-pypi-publish")
    assert PINNED.match(step["uses"])
    assert set(step.get("with", {})) <= {"packages-dir"}, "no password/token input: trusted publishing only"
    text = (WORKFLOWS / "pypi.yml").read_text(encoding="utf-8")
    assert "secrets." not in text and "PYPI_API_TOKEN" not in text and "password" not in text.lower()


# --- the scripts the workflows call -----------------------------------------------------------

@pytest.mark.parametrize("script,flags", [
    ("build_wheel.py", ["--version", "--web-from", "--out"]),
    ("check_wheel.py", ["--expect-version"]),
    ("smoke_uv_install.py", ["--wheel", "--expect-version"]),
    ("upgrade_check.py", ["--old", "--old-version", "--new", "--new-version", "--wait"]),
])
def test_workflow_flags_exist_on_the_scripts(script, flags):
    done = subprocess.run([sys.executable, str(SCRIPTS / script), "--help"], capture_output=True,
                          text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    for flag in flags:
        assert flag in done.stdout, f"{script} no longer takes {flag}"


# --- scripts/upgrade_check.py helpers ---------------------------------------------------------

def test_parse_status_reads_json_and_nothing_else():
    assert upgrade_check.parse_status('{"running": true}') == {"running": True}
    assert upgrade_check.parse_status("") is None
    assert upgrade_check.parse_status("Maestro is running.") is None
    assert upgrade_check.parse_status("[1, 2]") is None


def test_reports_version_needs_a_running_backend_on_that_version():
    up = {"running": True, "backend": {"version": "0.0.0.dev2"}}
    assert upgrade_check.reports_version(up, "0.0.0.dev2")
    assert not upgrade_check.reports_version(up, "0.0.0.dev1")
    assert not upgrade_check.reports_version({"running": False, "backend": {"version": "0.0.0.dev2"}}, "0.0.0.dev2")
    assert not upgrade_check.reports_version({"running": True, "backend": {"version": None}}, "0.0.0.dev2")
    assert not upgrade_check.reports_version({"running": True, "backend": None}, "0.0.0.dev2")
    assert not upgrade_check.reports_version(None, "0.0.0.dev2")


def test_snapshots_lists_only_backup_files(tmp_path):
    assert upgrade_check.snapshots(tmp_path) == []
    folder = tmp_path / "backups"
    folder.mkdir()
    (folder / "db-20261010-120000.sqlite3.gz").write_bytes(b"x")
    (folder / "notes.txt").write_text("not a snapshot")
    assert upgrade_check.snapshots(tmp_path) == ["db-20261010-120000.sqlite3.gz"]


def test_upgrade_check_backup_pattern_matches_the_maintenance_module():
    from maestro_cs import maintenance

    assert upgrade_check.BACKUP_GLOB == maintenance.BACKUP_GLOB
