import contextlib
import json
import os
import subprocess
import sys
import time

import psutil
import pytest

from maestro_cs import procs

SLEEPER = "import time; time.sleep(60)"


@pytest.fixture
def started():
    """Child processes a test started; every one is gone afterwards."""
    children: list[subprocess.Popen] = []
    yield children
    for child in children:
        with contextlib.suppress(psutil.Error):  # the test already ended it
            procs.kill_tree(psutil.Process(child.pid), grace=1.0)
        child.wait(timeout=5)


def sleeper(started):
    child = subprocess.Popen([sys.executable, "-c", SLEEPER])
    started.append(child)
    return child


def test_read_json_is_none_for_missing_garbage_and_non_objects(tmp_path):
    assert procs.read_json(tmp_path / "absent.json") is None
    (tmp_path / "bad.json").write_text("{nope", encoding="utf-8")
    assert procs.read_json(tmp_path / "bad.json") is None
    (tmp_path / "list.json").write_text("[1]", encoding="utf-8")
    assert procs.read_json(tmp_path / "list.json") is None


def test_write_json_replaces_atomically_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "ready.json"
    procs.write_json(target, {"a": 1})
    procs.write_json(target, {"a": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["ready.json"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_write_json_is_private_to_the_user(tmp_path):
    target = tmp_path / "supervisor.json"
    procs.write_json(target, {"a": 1})
    assert target.stat().st_mode & 0o777 == 0o600


def test_a_record_of_our_own_process_is_live(tmp_path):
    record_file = tmp_path / "me.json"
    procs.write_record(record_file, os.getpid(), role="test")
    record = procs.read_json(record_file)
    assert record["role"] == "test" and record["pid"] == os.getpid()
    live = procs.live_process(record)
    assert live is not None and live.pid == os.getpid()


def test_a_record_of_an_exited_process_is_not_live(tmp_path, started):
    child = sleeper(started)
    procs.write_record(tmp_path / "c.json", child.pid)
    record = procs.read_json(tmp_path / "c.json")
    assert procs.live_process(record) is not None
    procs.kill_tree(psutil.Process(child.pid), grace=1.0)
    child.wait(timeout=5)
    assert procs.live_process(record) is None


def test_a_reused_pid_is_not_our_process(tmp_path):
    procs.write_record(tmp_path / "me.json", os.getpid())
    record = procs.read_json(tmp_path / "me.json")
    record["create_time"] -= 100  # same pid, born earlier: someone else's process
    assert procs.live_process(record) is None


@pytest.mark.parametrize("record", [
    None, {}, {"pid": "x", "create_time": 1}, {"pid": 1}, {"pid": 99999999, "create_time": 1.0},
])
def test_unusable_records_are_not_live(record):
    assert procs.live_process(record) is None


@pytest.mark.skipif(os.name != "posix", reason="zombies are a POSIX state")
def test_a_zombie_is_not_live(tmp_path, started):
    child = sleeper(started)
    procs.write_record(tmp_path / "z.json", child.pid)
    record = procs.read_json(tmp_path / "z.json")
    os.kill(child.pid, 9)
    deadline = time.monotonic() + 5
    while psutil.Process(child.pid).status() != psutil.STATUS_ZOMBIE:
        assert time.monotonic() < deadline, "child never became a zombie"
        time.sleep(0.02)
    assert procs.live_process(record) is None


def test_kill_tree_kills_a_child_and_its_grandchild(started):
    code = ("import subprocess, sys, time\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(g.pid, flush=True)\n"
            "time.sleep(60)\n")
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    started.append(child)
    grandchild = psutil.Process(int(child.stdout.readline()))
    procs.kill_tree(psutil.Process(child.pid), grace=2.0)
    child.wait(timeout=5)
    child.stdout.close()
    assert not grandchild.is_running() or grandchild.status() == psutil.STATUS_ZOMBIE


@pytest.mark.skipif(os.name != "posix", reason="needs a child that survives SIGTERM")
def test_kill_tree_escalates_when_terminate_is_ignored(started):
    code = ("import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('up', flush=True)\n"
            "time.sleep(60)\n")
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    started.append(child)
    assert child.stdout.readline().strip() == "up"
    started_at = time.monotonic()
    procs.kill_tree(psutil.Process(child.pid), grace=0.5)
    assert child.wait(timeout=5) != 0
    assert time.monotonic() - started_at < 4
    child.stdout.close()


def test_kill_tree_on_a_process_that_is_already_gone_is_quiet(started):
    child = sleeper(started)
    handle = psutil.Process(child.pid)
    procs.kill_tree(handle, grace=1.0)
    child.wait(timeout=5)
    procs.kill_tree(handle, grace=0.1)  # must not raise
