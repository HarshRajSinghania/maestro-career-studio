"""Autonomous spike pages distinguish fill outcomes from broken measurements."""

import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import Request, urlopen

import pytest

from tests.browser.pages import in_both_windows, oracle


def spike():
    return importlib.import_module("scripts.spike_background_fill")


@pytest.fixture
def server():
    with spike().serve() as running:
        yield running


EXPECTED = [
    ("workday_text.html", {"city": "Springfield", "zip": "12345"}),
    ("workday_listbox.html", {"degree": "Masters", "auth": "No"}),
    ("workday_date.html", {"from": "2019-08", "signed": "2026-09-26"}),
]


def check_app(page, name, expected):
    assert {key: oracle(page, key) for key in expected} == expected
    if name == "workday_date.html":
        assert page.input_value("#n") == "2021-05"
        assert page.input_value("#p") == "05/2021"


@in_both_windows()
@pytest.mark.parametrize(("name", "expected"), EXPECTED)
def test_autonomous_driver_posts_a_verified_fill(window, request, server, name, expected):
    page = request.getfixturevalue(window)
    page.context.set_offline(False)
    trial, url = server.new_trial(name, "visible", delay=0, runs=1)
    server.arm([trial])
    page.goto(url)
    rows = server.collect([trial], timeout=5)[trial]
    assert len(rows) == 1
    assert rows[0]["ms"] > 0 and rows[0]["committed"] is True
    assert rows[0]["visibility_start"] == rows[0]["visibility_end"] == "visible"
    check_app(page, name, expected)


@pytest.mark.parametrize(("name", "expected"), EXPECTED)
def test_repeated_driver_samples_use_fresh_fixture_state(page, server, name, expected):
    page.context.set_offline(False)
    trial, url = server.new_trial(name, "visible", delay=0, runs=2)
    server.arm([trial])
    page.goto(url)
    rows = server.collect([trial], timeout=8)[trial]
    assert [row["run"] for row in rows] == [0, 1]
    assert all(row["committed"] is True for row in rows)
    check_app(page, name, expected)


@pytest.mark.parametrize("name", [name for name, _ in EXPECTED])
def test_driver_reports_an_engine_success_without_an_app_commit_as_a_failure(page, server, name):
    page.context.set_offline(False)
    trial, url = server.new_trial(name, "visible", delay=0, runs=1)
    page.goto(url)
    # The engine claims success; the fake app never received the writes.
    page.evaluate("""() => {
      window.careerStudioCompanion.fillOps.apply = async (actions) =>
        actions.map((a) => ({fid: a.fid, outcome: 'verified', committed: a.value ?? a.text}));
    }""")
    server.arm([trial])
    row = server.collect([trial], timeout=5)[trial][0]
    question = spike().FILLS[name][0]["question"]
    assert row["committed"] is False
    assert row["error"] == f"{question} did not commit"
    assert row["failure_step"] == f"oracle ({question})"
    assert type(row["raf_within_1s"]) is bool


def test_driver_reports_an_inventory_that_omits_a_field_as_a_failure(page, server):
    page.context.set_offline(False)
    trial, url = server.new_trial("workday_text.html", "visible", delay=0, runs=1)
    page.goto(url)
    page.evaluate("""() => {
      const ops = window.careerStudioCompanion.fillOps;
      const inventory = ops.inventory;
      ops.inventory = async (opts) => {
        const got = await inventory(opts);
        return {...got, fields: got.fields.filter((f) => f.question !== 'Postal Code')};
      };
    }""")
    server.arm([trial])
    row = server.collect([trial], timeout=5)[trial][0]
    assert row["committed"] is False
    assert "missing field Postal Code" in row["error"]
    assert row["failure_step"] == "inventory (Postal Code)"


@pytest.mark.parametrize("starved", [False, True])
def test_driver_records_failed_exploration_and_continues_all_attempts(page, server, starved):
    module = spike()
    name = "workday_listbox.html"
    # Install the failed operation with each fresh fixture, including resets.
    server.shells[name] += module.inline_script("""
      window.careerStudioCompanion.fillOps.explore = async (actions) =>
        Object.fromEntries(actions.map((a) => [a.fid, {complete: false, options: []}]));
    """)
    if starved:
        server.shells[name] += module.inline_script("""
          window.requestAnimationFrame = () => 123;
          window.cancelAnimationFrame = () => {};
        """)
    page.context.set_offline(False)
    trial, url = server.new_trial(name, "visible", delay=0, runs=2)
    server.arm([trial])
    page.goto(url)
    rows = server.collect([trial], timeout=6)[trial]
    assert [row["run"] for row in rows] == [0, 1]
    assert all(row["committed"] is False for row in rows)
    assert all(row["error"] == "could not explore Degree" for row in rows)
    assert all(row["failure_step"] == "explore (Degree)" for row in rows)
    assert all(row["raf_within_1s"] is (not starved) for row in rows)


@pytest.mark.parametrize(("elapsed", "expected"), [(16, True), (1_001, False)])
def test_raf_probe_checks_the_page_clock_when_the_timeout_is_delayed(page, elapsed, expected):
    page.evaluate("""(elapsed) => {
      let now = 0;
      performance.now = () => now;
      window.requestAnimationFrame = (callback) => setTimeout(() => {
        now = elapsed;
        callback(now);
      }, 0);
      window.cancelAnimationFrame = clearTimeout;
    }""", elapsed)
    assert page.evaluate(spike().RAF_PROBE_JS) is expected


def test_an_unreset_fixture_still_breaks_the_harness(page, server):
    page.context.set_offline(False)
    trial, url = server.new_trial("workday_text.html", "visible", delay=0, runs=1)
    page.goto(url)
    page.evaluate("window.__oracle.city = 'Springfield'")
    server.arm([trial])
    with pytest.raises(RuntimeError, match="fixture not reset"):
        server.collect([trial], timeout=5)


def test_driver_waits_for_the_host_to_arrange_the_tabs(page, server):
    page.context.set_offline(False)
    trial, url = server.new_trial("workday_text.html", "visible", delay=0, runs=1)
    page.goto(url)
    with pytest.raises(RuntimeError, match="timed out waiting"):
        server.collect([trial], timeout=0.05)
    assert oracle(page, "city") is None
    server.arm([trial])
    assert server.collect([trial], timeout=5)[trial][0]["committed"] is True


def test_driver_checks_its_own_visibility_before_the_scheduled_wait(page, server):
    page.context.set_offline(False)
    trial, url = server.new_trial("workday_text.html", "hidden", delay=0.02, runs=1)
    page.goto(url)
    server.arm([trial])
    with pytest.raises(RuntimeError, match="before scheduled wait"):
        server.collect([trial], timeout=3)
    assert oracle(page, "city") is None


def test_driver_checks_visibility_again_when_the_scheduled_timer_wakes(page, server):
    page.context.set_offline(False)
    trial, url = server.new_trial("workday_text.html", "visible", delay=0.05, runs=1)
    page.goto(url)
    page.evaluate("""() => {
      const fetch = window.fetch;
      window.fetch = async (...args) => {
        const response = await fetch(...args);
        if (args[0] === '/visibility' &&
            JSON.parse(args[1].body).phase === 'before scheduled wait') {
          Object.defineProperty(document, 'visibilityState', {value: 'hidden'});
        }
        return response;
      };
    }""")
    server.arm([trial])
    with pytest.raises(RuntimeError, match="before fill"):
        server.collect([trial], timeout=3)
    assert oracle(page, "city") is None


def test_driver_reports_a_visibility_change_during_the_fill(page, server):
    page.context.set_offline(False)
    trial, url = server.new_trial("workday_text.html", "visible", delay=0, runs=1)
    page.goto(url)
    page.evaluate("""() => {
      const ops = window.careerStudioCompanion.fillOps;
      const apply = ops.apply;
      ops.apply = async (actions) => {
        const got = await apply(actions);
        document.dispatchEvent(new Event('visibilitychange'));
        return got;
      };
    }""")
    server.arm([trial])
    with pytest.raises(RuntimeError, match="visibility changed"):
        server.collect([trial], timeout=5)


def receipt(trial):
    return {
        "trial": trial, "run": 0, "fixture": "workday_text.html", "ms": 12.5,
        "visibility_start": "hidden", "visibility_end": "hidden", "committed": True,
        "visibility_changed": False, "hidden_ms": 10_001, "raf_within_1s": False,
    }


@pytest.mark.parametrize(("changes", "error"), [
    ({"visibility_start": "visible"}, "expected hidden"),
    ({"visibility_end": "visible"}, "expected hidden"),
    ({"committed": "true"}, "invalid commit status"),
    ({"ms": 0}, "invalid duration"),
    ({"ms": float("nan")}, "invalid duration"),
    ({"ms": True}, "invalid duration"),
    ({"fixture": "workday_date.html"}, "wrong fixture"),
    ({"run": 5}, "invalid run"),
    ({"visibility_changed": True}, "visibility changed"),
    ({"hidden_ms": 9_999}, "background wait too short"),
    ({"raf_within_1s": None}, "invalid rAF diagnostic"),
    ({"committed": False, "error": "City did not commit"}, "missing failure diagnostic"),
])
def test_server_refuses_invalid_samples(server, changes, error):
    trial, _ = server.new_trial("workday_text.html", "hidden", delay=10, hidden_ms=10_000)
    with pytest.raises(ValueError, match=error):
        server.accept_result({**receipt(trial), **changes})


@pytest.mark.parametrize("key", ["visibility_start", "visibility_end"])
def test_wrong_visibility_is_fatal_even_for_a_failed_fill(server, key):
    trial, _ = server.new_trial("workday_text.html", "hidden", delay=0, runs=1)
    row = {**receipt(trial), "committed": False, "error": "City did not commit",
           "failure_step": "oracle (City)", key: "visible"}
    with pytest.raises(ValueError, match="expected hidden"):
        server.accept_result(row)


def test_server_accepts_failures_and_report_uses_only_successful_durations(server, capsys):
    name = "workday_listbox.html"
    trial, _ = server.new_trial(name, "hidden", delay=0)
    base = {**receipt(trial), "fixture": name}
    failure = {**base, "committed": False, "error": "could not explore Degree",
               "failure_step": "explore (Degree)"}
    for row in [
        {**base, "run": 0, "ms": 10},
        {**failure, "run": 1, "ms": 99_000},
        {**base, "run": 2, "ms": 30},
        {**failure, "run": 3, "ms": 88_000},
        {**failure, "run": 4, "ms": 77_000, "error": "fill failed for Degree: timeout",
         "failure_step": "apply (Degree)"},
    ]:
        server.accept_result(row)
    rows = server.collect([trial], timeout=1)[trial]
    module = spike()
    assert module.summarize(rows) == {
        "successes": 2, "attempts": 5, "median_ms": 20,
        "failures": {"could not explore Degree": 2, "fill failed for Degree: timeout": 1},
    }
    module.print_report({"background (immediate)": {name: rows}})
    output = capsys.readouterr().out
    assert "2/5" in output and "20.0" in output
    assert "2x could not explore Degree" in output
    assert "1x fill failed for Degree: timeout" in output
    diagnostics = [line for line in output.splitlines() if line.startswith("failure:")]
    assert len(diagnostics) == 3
    assert all(name in line and "background (immediate)" in line for line in diagnostics)
    assert all("rAF within 1 s=no" in line for line in diagnostics)
    assert "step=explore (Degree)" in diagnostics[0]
    assert "step=apply (Degree)" in diagnostics[-1]


def test_report_handles_no_successful_fills(capsys):
    rows = [{**receipt("unused"), "run": run, "committed": False,
             "error": "City did not commit", "failure_step": "oracle (City)"}
            for run in range(5)]
    module = spike()
    assert module.summarize(rows)["median_ms"] is None
    module.print_report({"front": {"workday_text.html": rows}})
    output = capsys.readouterr().out
    assert "0/5" in output and "N/A" in output
    assert "5x City did not commit" in output


def test_an_immediate_fill_failure_can_have_zero_elapsed_time(server):
    trial, _ = server.new_trial("workday_text.html", "hidden", delay=0, runs=1)
    server.accept_result({**receipt(trial), "committed": False, "ms": 0,
                          "error": "missing field City", "failure_step": "inventory (City)"})
    assert server.collect([trial], timeout=1)[trial][0]["committed"] is False


def test_missing_posts_and_a_dead_browser_remain_harness_errors(server):
    trial, _ = server.new_trial("workday_text.html", "hidden", delay=0, runs=1)
    with pytest.raises(RuntimeError, match="timed out waiting"):
        server.collect([trial], timeout=0.01)
    process = SimpleNamespace(poll=lambda: 7, returncode=7)
    with pytest.raises(RuntimeError, match="Chromium exited with status 7"):
        server.collect([trial], timeout=1, process=process)


def test_server_refuses_duplicate_samples(server):
    trial, _ = server.new_trial("workday_text.html", "hidden", delay=0, runs=1)
    server.accept_result(receipt(trial))
    with pytest.raises(ValueError, match="duplicate"):
        server.accept_result(receipt(trial))


def test_http_post_rejection_is_an_error_not_a_sample(server):
    trial, _ = server.new_trial("workday_text.html", "hidden", delay=0, runs=1)
    row = {**receipt(trial), "visibility_end": "visible"}
    request = Request(server.base_url + "/results", json.dumps(row).encode(),
                      {"Content-Type": "application/json"}, method="POST")
    with pytest.raises(HTTPError) as rejected:
        urlopen(request, timeout=2)
    assert rejected.value.code == 400
    with pytest.raises(RuntimeError, match="expected hidden"):
        server.collect([trial], timeout=1)


@pytest.mark.parametrize(("visibility", "names"), [
    ("visible", ["workday_text.html"]),
    ("hidden", ["workday_text.html"]),
    ("hidden", ["workday_text.html", "workday_listbox.html"]),
])
def test_every_new_tab_restores_the_intended_foreground(monkeypatch, server, visibility, names):
    module = spike()
    commands = []
    targets = {}

    def debug(_base, path, **_kwargs):
        commands.append(path)
        if path.startswith('/json/new?'):
            target = str(len(targets))
            targets[target] = unquote(path.split('?', 1)[1])
            return {"id": target}

    arm = server.arm

    def armed(trials):
        arm(trials)
        for trial in trials:
            for run in range(module.RUNS):
                server.accept_result({**receipt(trial), "run": run,
                                      "fixture": server.trials[trial].fixture,
                                      "visibility_start": visibility, "visibility_end": visibility})

    monkeypatch.setattr(module, "debug_http", debug)
    monkeypatch.setattr(server, "arm", armed)
    try:
        result = module.measure(server, "debug", "holder", None, names, visibility=visibility)
        assert set(result) == set(names)
        expected = '0' if visibility == 'visible' else 'holder'
        for index, command in enumerate(commands):
            if command.startswith('/json/new?'):
                assert commands[index + 1] == '/json/activate/' + expected
    finally:
        # Teardown's arm only releases waiting HTTP handlers.
        monkeypatch.setattr(server, "arm", arm)


def test_six_minute_lone_fixture_waits_behind_the_holder(monkeypatch):
    module = spike()
    # This test exercises tab arrangement and the real trial bookkeeping,
    # without a listening HTTP socket or a six-minute sleep.
    with monkeypatch.context() as no_listener:
        no_listener.setattr(module.SpikeServer, "server_bind", lambda self: setattr(self, "server_port", 0))
        no_listener.setattr(module.SpikeServer, "server_activate", lambda self: None)
        server = module.SpikeServer()
    tabs = {"holder": server.base_url + "/idle"}
    active = "holder"
    arm = server.arm

    def debug(_base, path, **_kwargs):
        nonlocal active
        if path.startswith('/json/new?'):
            active = str(len(tabs))  # Creating a target brings it to the front.
            tabs[active] = unquote(path.split('?', 1)[1])
            return {"id": active}
        if path.startswith('/json/activate/'):
            active = path.rsplit('/', 1)[1]
        elif path.startswith('/json/close/'):
            del tabs[path.rsplit('/', 1)[1]]

    def armed(trials):
        # Exercise the real lone-fixture case, without an artificial idle slot.
        assert len(tabs) == 2
        assert active == "holder"
        arm(trials)
        for trial in trials:
            url = next(url for url in tabs.values() if trial in url)
            assert parse_qs(urlsplit(url).query)["delay"] == ["360"]
            # The in-page driver's existing visibility checks guard both the
            # scheduled wait's start and its wake-up (tested separately above).
            server.check_visibility({"trial": trial, "visibility": "hidden",
                                     "phase": "before scheduled wait"})
            for run in range(module.RUNS):
                server.accept_result({**receipt(trial), "run": run, "hidden_ms": 360_001})

    monkeypatch.setattr(module, "debug_http", debug)
    monkeypatch.setattr(server, "arm", armed)
    try:
        result = module.measure(server, "debug", "holder", None, ["workday_text.html"],
                                visibility="hidden", delay=360)
        assert len(result["workday_text.html"]) == module.RUNS
        assert tabs == {"holder": server.base_url + "/idle"}
    finally:
        monkeypatch.setattr(server, "arm", arm)
        server.server_close()


@pytest.mark.parametrize("mismatches", [1, 3, 4])
def test_visibility_retries_replace_only_the_failed_trial(monkeypatch, server, mismatches):
    module = spike()
    opened = []
    activated = []
    arm = server.arm

    def debug(_base, path, **_kwargs):
        if path.startswith('/json/new?'):
            url = unquote(path.split('?', 1)[1])
            opened.append(url)
            return {"id": str(len(opened))}
        if path.startswith('/json/activate/'):
            activated.append(path)

    def armed(trials):
        arm(trials)
        for trial in trials:
            name = server.trials[trial].fixture
            count = sum(urlsplit(url).path == '/' + name for url in opened)
            base = {**receipt(trial), "fixture": name, "hidden_ms": 10_001}
            if name == 'workday_text.html' and count <= mismatches:
                # A partially completed trial must be discarded on retry.
                server.accept_result({**base, "ms": 99_999})
                row = {**base, "run": 1, "visibility_end": "visible"}
                request = Request(server.base_url + '/results', json.dumps(row).encode(),
                                  {"Content-Type": "application/json"}, method="POST")
                with pytest.raises(HTTPError):
                    urlopen(request, timeout=2)
            else:
                for run in range(module.RUNS):
                    server.accept_result({**base, "run": run, "ms": 20})

    monkeypatch.setattr(module, "debug_http", debug)
    monkeypatch.setattr(server, "arm", armed)
    try:
        if mismatches == 4:
            with pytest.raises(RuntimeError, match="after 3 visibility retries"):
                module.measure(server, 'debug', 'holder', None, list(module.FILLS)[:2],
                               visibility='hidden', delay=10)
        else:
            result = module.measure(server, 'debug', 'holder', None, list(module.FILLS)[:2],
                                    visibility='hidden', delay=10)
            assert all(len(rows) == module.RUNS for rows in result.values())
            assert all(row['ms'] == 20 for rows in result.values() for row in rows)
        assert sum('/workday_text.html?' in url for url in opened) == min(mismatches + 1, 4)
        assert sum('/workday_listbox.html?' in url for url in opened) == 1
        assert all(parse_qs(urlsplit(url).query)['delay'] == ['10'] for url in opened)
        assert len(activated) >= len(opened)
        assert all(path == '/json/activate/holder' for path in activated)
    finally:
        monkeypatch.setattr(server, "arm", arm)


def test_main_flushes_finished_cases_before_a_later_failure(monkeypatch):
    module = spike()
    output = io.StringIO()
    flushed = []

    class Output:
        def write(self, value):
            return output.write(value)

        def flush(self):
            flushed.append(output.getvalue())

    class Locator:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(executable_path='/fake/Chromium'))

        def __exit__(self, *_args):
            pass

    def measure(_server, _url, _holder, _process, names, *, visibility, delay=0):
        if delay:
            assert any('workday_date.html' in line and 'background (immediate)' in line
                       and '1/1' in line for text in flushed for line in text.splitlines())
            raise RuntimeError('delayed harness failed')
        return {name: [{**receipt('unused'), 'fixture': name}] for name in names}

    process = SimpleNamespace(terminate=lambda: None, wait=lambda **_kwargs: 0)
    monkeypatch.setattr(module, 'sync_playwright', Locator)
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *_args, **_kwargs: process)
    monkeypatch.setattr(module, 'debugging_url', lambda *_args: ('debug', 'holder'))
    monkeypatch.setattr(module, 'measure', measure)
    monkeypatch.setattr(sys, 'stdout', Output())
    monkeypatch.setattr(sys, 'argv', ['spike_background_fill', '--quick'])
    with pytest.raises(RuntimeError, match='delayed harness failed'):
        module.main()
    assert any('workday_date.html' in line and 'front' in line and '1/1' in line
               for text in flushed for line in text.splitlines())


@pytest.mark.parametrize("needs_kill", [False, True])
def test_standalone_launch_releases_playwright_and_cleans_up_on_failure(monkeypatch, needs_kill):
    module = spike()
    released = False
    launched = {}

    class Locator:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(executable_path="/fake/Chromium"))

        def __exit__(self, *_args):
            nonlocal released
            released = True

    class Process:
        terminated = killed = False
        waits = 0

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout):
            self.waits += 1
            if needs_kill and self.waits == 1:
                raise subprocess.TimeoutExpired("Chromium", timeout)
            return 0

    process = Process()

    def launch(argv, **_kwargs):
        assert released
        assert argv[0] == "/fake/Chromium"
        port = next(arg.split("=", 1)[1] for arg in argv
                    if arg.startswith("--remote-debugging-port="))
        # Port 0 itself enables AutomationControlled in Chromium.
        assert int(port) > 0
        assert "--disable-backgrounding-occluded-windows" in argv
        for flag in ("--enable-automation", "--headless", "--remote-debugging-pipe",
                     "--disable-background-timer-throttling", "--disable-renderer-backgrounding"):
            assert not any(arg.startswith(flag) for arg in argv)
        launched["profile"] = Path(next(arg.split("=", 1)[1] for arg in argv
                                       if arg.startswith("--user-data-dir=")))
        assert launched["profile"].is_dir()
        return process

    def fail_startup(*_args):
        raise RuntimeError("startup failed")

    monkeypatch.setattr(module, "sync_playwright", Locator)
    monkeypatch.setattr(module.subprocess, "Popen", launch)
    monkeypatch.setattr(module, "debugging_url", fail_startup)
    monkeypatch.setattr(sys, "argv", ["spike_background_fill", "--quick"])
    with pytest.raises(RuntimeError, match="startup failed"):
        module.main()
    assert process.terminated and process.killed is needs_kill
    assert not launched["profile"].exists()
