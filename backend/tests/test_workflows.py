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


def _named_step(job: dict, prefix: str) -> dict:
    found = [s for s in _steps(job) if s.get("name", "").startswith(prefix)]
    assert len(found) == 1, f"expected one step named {prefix!r}, found {len(found)}"
    return found[0]


def test_maestro_cli_verifies_the_rendered_unit_with_systemd_analyze_on_linux():
    """Plan gate G6 claims this runs; nothing else in CI calls systemd-analyze."""
    step = _named_step(_workflow("ci.yml")["jobs"]["maestro-cli"], "Verify the rendered systemd unit")
    assert (step["if"], step["shell"]) == ("runner.os == 'Linux'", "python")
    run = step["run"]
    assert "autostart.render_unit(" in run
    assert 'shutil.which("systemd-analyze")' in run  # a missing tool: a notice, not a failure


def test_the_systemd_step_survives_a_runner_without_a_user_manager_but_fails_on_unit_errors():
    run = _named_step(_workflow("ci.yml")["jobs"]["maestro-cli"], "Verify the rendered systemd unit")["run"]
    # a runner has no user session: give the tool a runtime dir, skip man-page lookups, and add
    # --recursive-errors=no only when this systemd-analyze lists it
    expected = ['env["XDG_RUNTIME_DIR"]', '"--man=no"', 'if "--recursive-errors" in help_text',
                '"--recursive-errors=no"', '"verify"', '"--user"',
                # only a line that names OUR unit fails the step
                "autostart.UNIT_NAME in line"]
    for text in expected:
        assert text in run, text
    # a manager or bus failure that names nothing of ours is only a warning
    assert run.index("::error::") < run.index("::warning::")


def test_maestro_cli_creates_queries_and_deletes_the_logon_task_on_windows():
    step = _named_step(_workflow("ci.yml")["jobs"]["maestro-cli"], "Create, query and delete the logon task")
    assert (step["if"], step["shell"]) == ("runner.os == 'Windows'", "python")
    assert "admin runner" in step["name"]  # it does not prove the standard-user case
    run = step["run"]
    assert 'name = "Maestro CI Test"' in run
    assert "autostart.render_task_xml(" in run
    assert '"/XML", str(xml), "/F"], check=True' in run
    assert '"utf-16-le"' in run  # the encoding `autostart on` writes


def test_the_windows_task_is_created_then_queried_then_deleted_even_after_a_failure():
    run = _named_step(_workflow("ci.yml")["jobs"]["maestro-cli"], "Create, query and delete the logon task")["run"]
    create, query, delete = (run.index(f'"/{verb}"') for verb in ("Create", "Query", "Delete"))
    assert create < query < run.index("finally:") < delete


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


def test_pypi_release_workflow_queues_and_uses_no_shared_caches():
    doc = _workflow("pypi.yml")
    assert doc["concurrency"] == {"group": "pypi-${{ github.ref }}", "cancel-in-progress": False}
    for name, job in doc["jobs"].items():
        for step in _steps(job):
            if str(step.get("uses", "")).startswith("actions/setup-node@"):
                assert "cache" not in step.get("with", {}), f"{name}: no npm cache in a release build"
            if str(step.get("uses", "")).startswith("actions/cache@"):
                assert name == "smoke", "only the smoke job may use the model cache"


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
    # a manual run started from a tag must stay a dry run
    assert publish["if"] == "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"
    assert "github.event_name == 'push'" in publish["if"]
    assert not [s for s in _steps(publish) if "run" in s], "publish only downloads and publishes"
    assert not [s for s in _steps(publish) if str(s.get("uses", "")).startswith("actions/checkout@")]
    upload = _step_using(jobs["build"], "actions/upload-artifact")["with"]["name"]
    assert _step_using(publish, "actions/download-artifact")["with"]["name"] == upload
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


def test_update_log_state_reads_the_windows_scripts_own_lines():
    assert upgrade_check.update_log_state("") == "running"
    assert upgrade_check.update_log_state("[update] running uv\r\n") == "running"
    assert upgrade_check.update_log_state("[update] upgraded\n[update] finished\n") == "finished"
    failed = "[update] The upgrade failed. Your data is untouched.\n[update] finished\n"
    assert upgrade_check.update_log_state(failed) == "failed"  # it restarts the old version, then finishes


def test_update_script_lines_match_what_upgrade_check_waits_for():
    from maestro_cs import update

    script = update.windows_script(wait_pids=[1], uv_argv=["uv"], shim=Path("m.exe"), restart=True,
                                   snapshot=None, old="0.0.0.dev1")
    assert upgrade_check.UPDATE_FINISHED in script
    assert upgrade_check.UPDATE_FAILED in script


class _Clock:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_wait_for_update_log_returns_when_the_script_finishes(tmp_path):
    log = tmp_path / "update.log"
    ticks = _Clock()
    log.write_text("[update] running uv\n", encoding="utf-8")

    def sleep(seconds):
        ticks.sleep(seconds)
        log.write_text("[update] running uv\n[update] upgraded\n[update] finished\n", encoding="utf-8")

    text = upgrade_check.wait_for_update_log(log, 60, sleep=sleep, clock=ticks.clock)
    assert "[update] upgraded" in text


def test_wait_for_update_log_fails_at_once_on_the_failure_line(tmp_path):
    log = tmp_path / "update.log"
    log.write_text("[update] running uv\n[update] The upgrade failed. Your data is untouched.\n", encoding="utf-8")
    ticks = _Clock()
    with pytest.raises(upgrade_check.Failure, match="failed upgrade"):
        upgrade_check.wait_for_update_log(log, 60, sleep=ticks.sleep, clock=ticks.clock)
    assert ticks.now == 0.0  # did not wait


def test_wait_for_update_log_times_out_with_the_log_tail(tmp_path):
    log = tmp_path / "update.log"
    ticks = _Clock()
    log.write_text("[update] waiting for maestro to exit (4)\n", encoding="utf-8")
    with pytest.raises(upgrade_check.Failure, match=r"(?s)did not reach .*waiting for maestro to exit"):
        upgrade_check.wait_for_update_log(log, 10, sleep=ticks.sleep, clock=ticks.clock)
    assert ticks.now >= 10


def test_wait_for_update_log_survives_a_log_that_does_not_exist_yet(tmp_path):
    ticks = _Clock()
    with pytest.raises(upgrade_check.Failure, match="did not reach"):
        upgrade_check.wait_for_update_log(tmp_path / "update.log", 4, sleep=ticks.sleep, clock=ticks.clock)


def test_upgrade_check_backup_pattern_matches_the_maintenance_module():
    from maestro_cs import maintenance

    assert upgrade_check.BACKUP_GLOB == maintenance.BACKUP_GLOB
