"""Argument parsing and error handling of the `maestro` command."""
import pytest

from maestro_cs import cli, control, package_version
from maestro_cs.home import HomeError


class Recorder:
    """Stands in for Controller: remembers the call and returns a chosen code."""

    calls: list = []
    code = 0

    def __init__(self, ctx):
        self.ctx = ctx

    def __getattr__(self, name):
        def call(*args, **kwargs):
            Recorder.calls.append((name, args, kwargs))
            return Recorder.code
        return call


@pytest.fixture
def rec(monkeypatch, tmp_path):
    Recorder.calls, Recorder.code = [], 0
    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(control, "Controller", Recorder)
    return Recorder


def test_no_command_prints_short_help_and_exits_zero(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "usage: maestro" in out and "start" in out and "status" in out


def test_version(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"maestro-career-studio {package_version()}"


def test_usage_errors_return_two(capsys):
    assert cli.main(["nonsense"]) == 2
    assert "invalid choice" in capsys.readouterr().err


def test_start_flags_reach_the_controller(rec):
    assert cli.main(["start", "--no-browser", "--skip-model"]) == 0
    assert rec.calls == [("start", (), {"no_browser": True, "skip_model": True})]


def test_start_defaults(rec):
    cli.main(["start"])
    assert rec.calls == [("start", (), {"no_browser": False, "skip_model": False})]


@pytest.mark.parametrize("verb", ["stop", "restart", "open"])
def test_simple_verbs(rec, verb):
    assert cli.main([verb]) == 0
    assert rec.calls == [(verb, (), {})]


def test_status_flags_and_exit_code_pass_through(rec):
    rec.code = 3
    assert cli.main(["status", "--json", "--offline"]) == 3
    assert rec.calls == [("status", (), {"as_json": True, "offline": True})]


def test_logs_arguments(rec):
    cli.main(["logs"])
    cli.main(["logs", "web", "-n", "5", "-f"])
    assert rec.calls == [("logs", ("backend", 50, False), {}), ("logs", ("web", 5, True), {})]


def test_logs_rejects_an_unknown_log(capsys):
    assert cli.main(["logs", "nope"]) == 2


def test_run_goes_to_the_supervisor_function(monkeypatch, tmp_path):
    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(control, "run", lambda ctx: 7)
    assert cli.main(["run"]) == 7


def test_a_port_problem_is_one_sentence_not_a_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAESTRO_PORT", "banana")
    assert cli.main(["status"]) == 1
    err = capsys.readouterr().err
    assert err.strip() == "MAESTRO_PORT must be a port number from 1 to 65535."


def test_home_and_control_errors_exit_one(monkeypatch, capsys):
    def bad_home():
        raise HomeError("Cannot set up /x: Permission denied.")

    monkeypatch.setattr(control, "load_context", bad_home)
    assert cli.main(["stop"]) == 1
    assert "Cannot set up /x" in capsys.readouterr().err

    def refuse():
        raise control.ControlError("Port 1 is busy.")

    monkeypatch.setattr(control, "load_context", refuse)
    assert cli.main(["start"]) == 1
    assert "Port 1 is busy." in capsys.readouterr().err


def test_ctrl_c_is_not_a_traceback(monkeypatch):
    def interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr(control, "load_context", interrupt)
    assert cli.main(["status"]) == 130
