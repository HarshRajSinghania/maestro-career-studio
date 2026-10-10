"""The supervisor against tiny Python HTTP servers (not uvicorn or node).

`signal.signal` only works on the main thread, so `Supervisor.run()` runs on the pytest main
thread here; a helper thread creates `run/stop.request`. Signals are tested through a subprocess.
"""
import io
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import psutil
import pytest

from maestro_cs import procs, supervisor
from maestro_cs.supervisor import ChildSpec, Supervisor

POSIX = os.name == "posix"

# argv: port pidfile [option ...]. Options:
#   unhealthy             /health answers 500
#   ignore_term           ignores SIGTERM (and SIGBREAK on Windows)
#   die_after_health=S    exits 3 about S seconds after the first /health it answers
#   die_once=FILE         like die_after_health=0.05, but only while FILE does not exist
FAKE_SERVER = textwrap.dedent('''
    import os, signal, sys, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    port, pidfile, options = int(sys.argv[1]), sys.argv[2], sys.argv[3:]
    opt = dict(o.partition("=")[::2] for o in options)
    with open(pidfile, "w") as handle:
        handle.write(str(os.getpid()))
    print("boot", flush=True)
    if "ignore_term" in opt:
        for name in ("SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), signal.SIG_IGN)
    die_after = float(opt["die_after_health"]) if "die_after_health" in opt else None
    if "die_once" in opt and not os.path.exists(opt["die_once"]):
        open(opt["die_once"], "w").close()
        die_after = 0.05
    armed = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.path in ("/health", "/api/version") and "unhealthy" not in opt
            self.send_response(200 if ok else 500)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            if ok and die_after is not None and not armed:
                armed.append(1)
                threading.Timer(die_after, lambda: os._exit(3)).start()

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
''')


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def pid_alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


@pytest.fixture
def fleet(tmp_path):
    """Builds fake-server specs; teardown stops anything still alive and fails if there was any."""
    run_dir = tmp_path / "run"
    logs = tmp_path / "logs"
    logs.mkdir()
    pidfiles: list[Path] = []

    class Fleet:
        def __init__(self):
            self.run_dir, self.logs = run_dir, logs
            self.ports: dict[str, int] = {}

        def spec(self, name, *options, health_timeout=20.0):
            port = self.ports[name] = free_port()
            pidfile = tmp_path / f"{name}.pid"
            pidfiles.append(pidfile)
            return ChildSpec(
                name=name,
                argv=[sys.executable, "-c", FAKE_SERVER, str(port), str(pidfile), *options],
                env=dict(os.environ), cwd=tmp_path, log=logs / f"{name}.log",
                health_url=f"http://127.0.0.1:{port}/health", health_timeout=health_timeout,
            )

        def pids(self):
            found = []
            for pidfile in pidfiles:
                try:
                    found.append(int(pidfile.read_text()))
                except (OSError, ValueError):
                    pass
            return found

        def supervisor(self, specs, **kwargs):
            defaults = dict(poll=0.05, grace=1.0, out=io.StringIO())
            return Supervisor(run_dir, specs, **{**defaults, **kwargs})

    helper = Fleet()
    yield helper
    (run_dir).mkdir(exist_ok=True)
    (run_dir / "stop.request").write_text("", encoding="utf-8")
    leftovers = [pid for pid in helper.pids() if pid_alive(pid)]
    for pid in leftovers:
        try:
            procs.kill_tree(psutil.Process(pid), grace=1.0)
        except psutil.Error:
            pass
    assert not leftovers, f"a test leaked child processes: {leftovers}"


def request_stop_when(run_dir: Path, condition, *, timeout: float = 20.0) -> dict:
    """On a helper thread: wait for `condition()`, snapshot the run dir, then ask for the stop."""
    snapshot: dict = {}

    def work():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not condition():
            time.sleep(0.02)
        snapshot["files"] = sorted(p.name for p in run_dir.glob("*.json"))
        for name in ("ready", "supervisor", "backend", "web"):
            snapshot[name] = procs.read_json(run_dir / f"{name}.json")
        snapshot["at"] = time.monotonic()
        (run_dir / "stop.request").write_text("", encoding="utf-8")

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    snapshot["thread"] = thread
    return snapshot


def wait_for_file(path: Path):
    return lambda: path.exists()


def test_starts_in_order_writes_the_records_and_stops_on_request(fleet):
    backend, web = fleet.spec("backend"), fleet.spec("web")
    sup = fleet.supervisor([backend, web], ready={"version": "9.9.9", "ports": {"web": 3000}})
    snap = request_stop_when(fleet.run_dir, wait_for_file(fleet.run_dir / "ready.json"))

    code = sup.run()
    snap["thread"].join(5)

    assert code == 0
    assert snap["files"] == ["backend.json", "ready.json", "supervisor.json", "web.json"]
    ready = snap["ready"]
    assert ready["version"] == "9.9.9" and ready["ports"] == {"web": 3000}
    assert ready["backend"] == snap["backend"]["pid"] and ready["web"] == snap["web"]["pid"]
    assert snap["supervisor"]["pid"] == os.getpid()
    assert "create_time" in snap["supervisor"] and "create_time" in snap["backend"]
    log = sup.out.getvalue()
    assert log.index("Started backend") < log.index("Started web")
    # Everything is cleaned up: records, ready.json, the stop request, and both servers.
    assert list(fleet.run_dir.iterdir()) == []
    assert not [pid for pid in fleet.pids() if pid_alive(pid)]


def test_stop_request_returns_within_two_seconds(fleet):
    sup = fleet.supervisor([fleet.spec("backend"), fleet.spec("web")])
    snap = request_stop_when(fleet.run_dir, wait_for_file(fleet.run_dir / "ready.json"))
    code = sup.run()
    assert code == 0
    assert time.monotonic() - snap["at"] < 2.0
    assert list(fleet.run_dir.iterdir()) == []


def test_a_stale_stop_request_from_an_earlier_run_is_ignored(fleet):
    fleet.run_dir.mkdir()
    (fleet.run_dir / "stop.request").write_text("", encoding="utf-8")
    (fleet.run_dir / "ready.json").write_text("{}", encoding="utf-8")
    sup = fleet.supervisor([fleet.spec("backend")])
    snap = request_stop_when(fleet.run_dir, lambda: procs.read_json(fleet.run_dir / "backend.json"))
    # ready.json from before is replaced by this run's own, which names the new pid.
    assert sup.run() == 0
    assert snap["backend"] is not None


def test_a_child_that_exits_once_is_restarted(fleet, tmp_path):
    flag = tmp_path / "died.flag"
    backend = fleet.spec("backend", f"die_once={flag}")
    web = fleet.spec("web")
    sup = fleet.supervisor([backend, web])
    snap = request_stop_when(
        fleet.run_dir, lambda: sup.out.getvalue().count("Started backend") >= 2
        and procs.read_json(fleet.run_dir / "ready.json") is not None)

    assert sup.run() == 0
    snap["thread"].join(5)
    log = sup.out.getvalue()
    assert "backend exited with code 3; restarting." in log
    assert log.count("Started backend") == 2 and log.count("Started web") == 1
    assert list(fleet.run_dir.iterdir()) == []


def test_a_child_that_keeps_exiting_ends_the_run_and_stops_the_other(fleet):
    backend = fleet.spec("backend", "die_after_health=0.05")
    web = fleet.spec("web")
    sup = fleet.supervisor([web, backend])  # web is healthy and must be stopped when backend gives up

    assert sup.run() == 1
    log = sup.out.getvalue()
    assert "backend keeps exiting (code 3); giving up." in log
    assert log.count("backend exited with code 3; restarting.") == 3  # max_restarts
    assert log.count("Started backend") == 4
    assert not [pid for pid in fleet.pids() if pid_alive(pid)]
    assert list(fleet.run_dir.iterdir()) == []


def test_restarts_outside_the_window_do_not_add_up(fleet):
    sup = fleet.supervisor([fleet.spec("backend")], restart_window=0.2)
    assert sup._may_restart("backend") and sup._may_restart("backend") and sup._may_restart("backend")
    time.sleep(0.3)
    assert sup._may_restart("backend")
    assert len(sup.exits["backend"]) == 1


def test_a_child_that_never_becomes_healthy_fails_the_run_and_names_it(fleet):
    backend = fleet.spec("backend", "unhealthy", health_timeout=1.0)
    web = fleet.spec("web")
    sup = fleet.supervisor([backend, web])
    began = time.monotonic()
    assert sup.run() == 1
    assert time.monotonic() - began < 6
    log = sup.out.getvalue()
    assert "backend did not become healthy within 1 seconds." in log
    assert "Started web" not in log  # the next child never starts
    assert not [pid for pid in fleet.pids() if pid_alive(pid)]
    assert list(fleet.run_dir.iterdir()) == []


def test_a_child_that_exits_during_start_fails_the_run_with_its_exit_code(fleet):
    spec = fleet.spec("backend")
    spec.argv = [sys.executable, "-c", "import sys; sys.exit(7)"]
    sup = fleet.supervisor([spec])
    assert sup.run() == 1
    assert "backend exited during start with code 7; see backend.log." in sup.out.getvalue()


@pytest.mark.skipif(not POSIX, reason="SIGTERM is not deliverable to a Windows child")
def test_a_child_that_ignores_sigterm_is_killed_after_the_grace_period(fleet):
    backend = fleet.spec("backend", "ignore_term")
    sup = fleet.supervisor([backend], grace=1.0)
    snap = request_stop_when(fleet.run_dir, wait_for_file(fleet.run_dir / "ready.json"))
    began = time.monotonic()
    assert sup.run() == 0
    snap["thread"].join(5)
    elapsed = time.monotonic() - snap["at"]
    assert 0.9 <= elapsed < 5, f"killed after {elapsed:.1f}s, grace was 1s"
    assert time.monotonic() - began < 15
    assert not [pid for pid in fleet.pids() if pid_alive(pid)]


def test_a_previous_runs_log_is_kept_and_a_restart_appends(fleet, tmp_path):
    fleet.logs.joinpath("backend.log").write_text("old run\n", encoding="utf-8")
    fleet.logs.joinpath("backend.log.1").write_text("older run\n", encoding="utf-8")
    flag = tmp_path / "died.flag"
    sup = fleet.supervisor([fleet.spec("backend", f"die_once={flag}")])
    request_stop_when(fleet.run_dir, lambda: sup.out.getvalue().count("Started backend") >= 2
                      and procs.read_json(fleet.run_dir / "ready.json") is not None)

    assert sup.run() == 0
    assert fleet.logs.joinpath("backend.log.1").read_text(encoding="utf-8") == "old run\n"
    # Both the first process and its replacement logged into the one current file.
    assert fleet.logs.joinpath("backend.log").read_text(encoding="utf-8").count("boot") == 2


def test_a_missing_executable_is_a_sentence_not_a_traceback(fleet, tmp_path):
    spec = fleet.spec("web")
    spec.argv = [str(tmp_path / "no-such-node"), "server.js"]
    sup = fleet.supervisor([spec])
    assert sup.run() == 1
    log = sup.out.getvalue()
    assert "Cannot start web:" in log and "no-such-node" in log
    assert "Traceback" not in log
    assert list(fleet.run_dir.iterdir()) == []


def test_an_unwritable_log_is_a_sentence_too(fleet, tmp_path):
    spec = fleet.spec("web")
    spec.log = tmp_path / "missing-dir" / "web.log"
    sup = fleet.supervisor([spec])
    assert sup.run() == 1
    assert "Cannot start web:" in sup.out.getvalue()


def test_a_stop_during_start_returns_zero(fleet):
    backend = fleet.spec("backend", "unhealthy", health_timeout=30.0)
    sup = fleet.supervisor([backend])
    request_stop_when(fleet.run_dir, lambda: "Started backend" in sup.out.getvalue())
    began = time.monotonic()
    assert sup.run() == 0
    assert time.monotonic() - began < 5
    assert not [pid for pid in fleet.pids() if pid_alive(pid)]


def test_helpers_that_left_the_childs_process_group_do_not_outlive_it(fleet, tmp_path):
    helper_pid = tmp_path / "helper.pid"
    leaves_a_helper = FAKE_SERVER.replace(
        'print("boot", flush=True)',
        "import subprocess\n"
        f"open({str(helper_pid)!r}, 'w').write(str(subprocess.Popen("
        "[sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True).pid))")
    spec = fleet.spec("web")
    spec.argv[2] = leaves_a_helper
    sup = fleet.supervisor([spec])
    request_stop_when(fleet.run_dir, wait_for_file(fleet.run_dir / "ready.json"))
    assert sup.run() == 0
    helper = int(helper_pid.read_text())
    deadline = time.monotonic() + 3
    while pid_alive(helper) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not pid_alive(helper)


def test_signal_handlers_are_restored_after_the_run(fleet):
    import signal
    before = signal.getsignal(signal.SIGINT)
    sup = fleet.supervisor([fleet.spec("backend")])
    request_stop_when(fleet.run_dir, wait_for_file(fleet.run_dir / "ready.json"))
    assert sup.run() == 0
    assert signal.getsignal(signal.SIGINT) is before


def test_http_ok_ignores_proxy_settings(fleet, monkeypatch):
    sup = fleet.supervisor([fleet.spec("backend")])
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    request_stop_when(fleet.run_dir, lambda: procs.read_json(fleet.run_dir / "ready.json"))
    assert sup.run() == 0  # healthy through the bogus proxy only if loopback bypasses it


def test_http_ok_is_false_for_a_closed_port_and_a_bad_url():
    assert supervisor.http_ok(f"http://127.0.0.1:{free_port()}/health", timeout=0.5) is False
    assert supervisor.http_ok("not a url", timeout=0.5) is False


# Signals are delivered to a real process, so these run Supervisor in a subprocess.

RUNNER = textwrap.dedent('''
    import sys
    from pathlib import Path
    from maestro_cs.supervisor import ChildSpec, Supervisor

    run_dir, fake, port, pidfile = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    import os
    spec = ChildSpec("backend", [sys.executable, "-c", fake, port, pidfile], dict(os.environ),
                     run_dir.parent, run_dir.parent / "backend.log",
                     f"http://127.0.0.1:{port}/health", 20.0)
    sys.exit(Supervisor(run_dir, [spec], poll=0.05, grace=1.0).run())
''')


def run_in_subprocess(tmp_path: Path, **popen):
    port, pidfile = free_port(), tmp_path / "child.pid"
    run_dir = tmp_path / "run2"
    proc = subprocess.Popen(
        [sys.executable, "-c", RUNNER, str(run_dir), FAKE_SERVER, str(port), str(pidfile)],
        env=dict(os.environ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **popen)
    deadline = time.monotonic() + 20
    try:
        while not (run_dir / "ready.json").exists():
            assert proc.poll() is None, "the supervisor subprocess died before it was ready"
            assert time.monotonic() < deadline, "the supervisor never became ready"
            time.sleep(0.05)
    except BaseException:
        proc.kill()
        proc.wait(10)
        raise
    return proc, run_dir, int(pidfile.read_text())


@pytest.mark.skipif(not POSIX, reason="POSIX signals")
@pytest.mark.parametrize("sig_name", ["SIGTERM", "SIGINT", "SIGHUP"])
def test_a_posix_signal_stops_the_supervisor_cleanly(tmp_path, sig_name):
    import signal
    proc, run_dir, child_pid = run_in_subprocess(tmp_path)
    try:
        proc.send_signal(getattr(signal, sig_name))
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)
        if pid_alive(child_pid):
            psutil.Process(child_pid).kill()
    assert not pid_alive(child_pid)
    assert list(run_dir.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="CTRL_BREAK_EVENT needs Windows")
def test_ctrl_break_stops_the_supervisor_cleanly_on_windows(tmp_path):
    import signal
    proc, run_dir, child_pid = run_in_subprocess(
        tmp_path, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT)
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)
        if pid_alive(child_pid):
            psutil.Process(child_pid).kill()
    assert not pid_alive(child_pid)
    assert list(run_dir.iterdir()) == []
