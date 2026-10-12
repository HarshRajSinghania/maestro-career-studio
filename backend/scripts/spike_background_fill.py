"""Measure Companion fills in real foreground/background Chromium tabs.

Run from backend/: python -m scripts.spike_background_fill [--quick]
Needs a display. No automation client attaches to the measured browser:
pages run their own fills and POST results to a loopback HTTP server.
Uses fictional fixtures, never submits, and reports five-attempt fill outcomes.
"""

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from queue import Empty, Queue
import socket
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from playwright.sync_api import sync_playwright

from tests.browser.conftest import ENGINE_SOURCES, EXTENSION, FIXTURES


RUNS = 5
VISIBILITY_RETRIES = 3
# Exact action shapes/values from test_fill_core_fields.py; the full date is
# from test_fixture_fidelity.py's MM/DD/YYYY commit test.
FILLS = {
    "workday_text.html": [
        {"question": "City", "op": "write", "value": "Springfield", "oracle": "city"},
        {"question": "Postal Code", "op": "write", "value": "12345", "oracle": "zip"},
    ],
    "workday_listbox.html": [
        {"question": "Degree", "op": "choose", "text": "Masters", "oracle": "degree"},
        {"question": "Are you legally authorized to work in the United States?",
         "op": "choose", "text": "No", "oracle": "auth"},
    ],
    "workday_date.html": [
        {"question": "From", "op": "write", "value": "2019-08", "oracle": "from"},
        {"question": "Date", "op": "write", "value": "2026-09-26", "oracle": "signed"},
        {"question": "Graduation date", "op": "write", "value": "2021-05",
         "selector": "#n", "expected": "2021-05"},
        {"question": "End date", "op": "write", "value": "2021-05",
         "selector": "#p", "expected": "05/2021"},
    ],
}

FILL_JS = """async (spec, setStep) => {
  const ops = window.careerStudioCompanion.fillOps;
  setStep('inventory');
  const {fields} = await ops.inventory({consentForms: false, runId: `spike-${performance.now()}`});
  for (const wanted of spec) {
    setStep(`inventory (${wanted.question})`);
    const matches = fields.filter((f) => f.question === wanted.question);
    if (matches.length !== 1) throw new Error(`missing field ${wanted.question} (or ambiguous)`);
    const {fid, fp} = matches[0];
    if (wanted.op === 'choose') {
      setStep(`explore (${wanted.question})`);
      const explored = (await ops.explore([{fid, fp, term: null}]))[fid];
      if (!explored?.complete || !explored.options.some((o) => o.text === wanted.text))
        throw new Error(`could not explore ${wanted.question}`);
    }
    const action = wanted.op === 'write'
      ? {fid, fp, op: 'write', value: wanted.value}
      : {fid, fp, op: 'choose', text: wanted.text};
    setStep(`apply (${wanted.question})`);
    const results = await ops.apply([action]);
    if (results.length !== 1 || results[0].outcome !== 'verified')
      throw new Error(`fill failed for ${wanted.question}: ${results[0]?.outcome}`);
  }
  // Read the APP's held values, not just the engine verdict or the display.
  for (const wanted of spec) {
    setStep(`oracle (${wanted.question})`);
    if (wanted.oracle) {
      const oracle = window.__oracle;
      if (!oracle || !Object.prototype.hasOwnProperty.call(oracle, wanted.oracle)) {
        const error = new Error(`missing oracle key ${wanted.oracle}`);
        error.harness = true;
        throw error;
      }
      if (oracle[wanted.oracle] !== (wanted.value ?? wanted.text))
        throw new Error(`${wanted.question} did not commit`);
    } else if (document.querySelector(wanted.selector)?.value !== wanted.expected) {
      throw new Error(`${wanted.question} did not commit`);
    }
  }
}"""

RAF_PROBE_JS = """() => new Promise((resolve) => {
  const start = performance.now();
  const frame = requestAnimationFrame(() => {
    clearTimeout(timer);
    // A throttled timeout can run late. A frame arriving after the deadline
    // still counts as starved, even if its callback beats that late timeout.
    resolve(performance.now() - start <= 1000);
  });
  const timer = setTimeout(() => {
    cancelAnimationFrame(frame);
    resolve(false);
  }, 1000);
})"""

DRIVER_JS = """async (config) => {
  const fill = FILL_FUNCTION;
  const probeRAF = RAF_FUNCTION;
  let changes = 0;
  let hiddenAt = document.hidden ? performance.now() : null;
  const watch = () => {
    changes += 1;
    hiddenAt = document.hidden ? performance.now() : null;
  };
  document.addEventListener('visibilitychange', watch);
  // The host arms the trial only AFTER arranging the tabs. This request does
  // not attach a debugger. The scheduled delay then uses the page's own timer.
  const ready = await fetch(`/ready?trial=${config.trial}`);
  if (!ready.ok) throw new Error('trial was not armed');
  const checkVisibility = async (phase) => {
    const response = await fetch('/visibility', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({trial: config.trial, visibility: document.visibilityState, phase}),
    });
    return response.ok;
  };
  // Verify in the page before starting the wait, and again when its potentially
  // throttled timer wakes. A rejected trial is restarted by the host.
  if (!await checkVisibility('before scheduled wait')) return;
  const delay = Number(new URL(location.href).searchParams.get('delay'));
  await new Promise((resolve) => setTimeout(resolve, delay * 1000));
  for (let run = 0; run < config.runs; run++) {
    if (run) {
      // Fresh fixture + engine, without navigation or tab activation: the tab
      // keeps its hidden age, and these setup costs are outside the fill time.
      window.careerStudioCompanion = {};
      window.__oracle = {};
      document.open();
      document.write(config.shell);
      document.close();
      document.addEventListener('visibilitychange', watch);
    }
    const before = changes;
    if (!await checkVisibility('before fill')) return;
    // Diagnostic only: exclude the probe's wait from the per-page fill time.
    const rafWithin1s = await probeRAF();
    const start = performance.now();
    let step = 'fixture reset';
    const row = {
      trial: config.trial, run, fixture: config.fixture, committed: false,
      visibility_start: document.visibilityState,
      hidden_ms: hiddenAt === null ? 0 : start - hiddenAt,
      raf_within_1s: rafWithin1s,
    };
    try {
      // A reset that failed must not turn a previous commit into a fast sample.
      for (const wanted of config.spec) {
        const value = wanted.oracle ? window.__oracle?.[wanted.oracle]
          : document.querySelector(wanted.selector)?.value;
        const fresh = value === '' || (wanted.oracle && value === null);
        if (!fresh) throw new Error(`fixture not reset: ${wanted.question}`);
      }
      await fill(config.spec, (next) => { step = next; });
      row.committed = true;
    } catch (error) {
      row.error = String(error.message ?? error);
      row.failure_step = step;
      if (step === 'fixture reset' || error.harness) row.harness_error = row.error;
    }
    row.ms = performance.now() - start;
    row.visibility_end = document.visibilityState;
    row.visibility_changed = changes !== before;
    const response = await fetch('/results', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(row),
    });
    // A valid non-commit is an outcome. Reset and try the remaining samples.
    // Rejected receipts indicate a broken measurement and stop this driver.
    if (!response.ok) return;
  }
}""".replace("FILL_FUNCTION", f"({FILL_JS})").replace("RAF_FUNCTION", f"({RAF_PROBE_JS})")


def inline_script(source: str) -> str:
    return "<script>" + source.replace("</script", "<\\/script") + "</script>"


@dataclass
class Trial:
    fixture: str
    visibility: str
    runs: int
    hidden_ms: int
    armed: Event = field(default_factory=Event)
    seen: set[int] = field(default_factory=set)
    cancelled: bool = False


class VisibilityMismatch(ValueError):
    def __init__(self, trial_id, reason):
        super().__init__(reason)
        self.trial_id = trial_id


class CancelledTrial(ValueError):
    pass


class SpikeServer(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server_port}"
        self.trials: dict[str, Trial] = {}
        self.results = Queue()
        self.lock = Lock()
        self.closing = Event()
        engine = "".join(inline_script((EXTENSION / src).read_text(encoding="utf-8"))
                         for src in ENGINE_SOURCES)
        self.shells = {name: (FIXTURES / name).read_text(encoding="utf-8") + engine
                       for name in FILLS}

    def new_trial(self, fixture, visibility, *, delay, runs=RUNS, hidden_ms=0):
        trial_id = uuid4().hex
        self.trials[trial_id] = Trial(fixture, visibility, runs, hidden_ms)
        query = urlencode({"trial": trial_id, "delay": delay})
        return trial_id, f"{self.base_url}/{fixture}?{query}"

    def arm(self, trial_ids):
        for trial_id in trial_ids:
            self.trials[trial_id].armed.set()

    def page_html(self, trial_id):
        trial = self.trials[trial_id]
        shell = self.shells[trial.fixture]
        config = {"trial": trial_id, "fixture": trial.fixture, "runs": trial.runs,
                  "spec": FILLS[trial.fixture], "shell": shell}
        return shell + inline_script(f"({DRIVER_JS})({json.dumps(config)});")

    def trial_for(self, row):
        if not isinstance(row, dict) or row.get("trial") not in self.trials:
            raise ValueError("unknown trial")
        trial = self.trials[row["trial"]]
        if trial.cancelled:
            raise CancelledTrial("trial was cancelled")
        return trial

    def check_visibility(self, row):
        trial = self.trial_for(row)
        if row.get("visibility") not in ("visible", "hidden"):
            raise ValueError("invalid visibility report")
        if row["visibility"] != trial.visibility:
            raise VisibilityMismatch(row["trial"],
                                     f"expected {trial.visibility} tab, got {row['visibility']} "
                                     f"({row.get('phase')})")

    def accept_result(self, row):
        trial = self.trial_for(row)
        if row.get("fixture") != trial.fixture:
            raise ValueError("wrong fixture")
        run = row.get("run")
        if type(run) is not int or not 0 <= run < trial.runs:
            raise ValueError("invalid run")
        if type(row.get("committed")) is not bool:
            raise ValueError("invalid commit status")
        for key in ("visibility_start", "visibility_end"):
            if row.get(key) != trial.visibility:
                raise VisibilityMismatch(row["trial"],
                                         f"expected {trial.visibility} tab, got {row.get(key)} ({key})")
        if row.get("visibility_changed") is not False:
            raise VisibilityMismatch(row["trial"], "visibility changed during fill")
        if row.get("harness_error"):
            raise ValueError(row["harness_error"])
        ms = row.get("ms")
        if (type(ms) not in (int, float) or not math.isfinite(ms) or ms < 0
                or (row["committed"] and ms == 0)):
            raise ValueError("invalid duration")
        age = row.get("hidden_ms")
        if type(age) not in (int, float) or not math.isfinite(age):
            raise ValueError("invalid background age")
        if age < trial.hidden_ms:
            raise VisibilityMismatch(row["trial"], "background wait too short (tab was activated)")
        if type(row.get("raf_within_1s")) is not bool:
            raise ValueError("invalid rAF diagnostic")
        if not row["committed"] and any(
                not isinstance(row.get(key), str) or not row[key].strip()
                for key in ("error", "failure_step")):
            raise ValueError("missing failure diagnostic")
        with self.lock:
            if run in trial.seen:
                raise ValueError("duplicate sample")
            trial.seen.add(run)
            self.results.put(row)

    def collect(self, trial_ids, *, timeout, process=None, retry=None):
        rows = {trial_id: [] for trial_id in trial_ids}
        # Keep original keys for the caller. Replacing one trial must neither
        # discard another fixture's results nor shorten its own background wait.
        current = {trial_id: trial_id for trial_id in trial_ids}
        retries = dict.fromkeys(trial_ids, 0)
        deadlines = dict.fromkeys(trial_ids, time.monotonic() + timeout)
        while any(len(rows[key]) < self.trials[key].runs for key in rows):
            if process is not None and process.poll() is not None:
                raise RuntimeError(f"Chromium exited with status {process.returncode}")
            pending = [key for key in rows if len(rows[key]) < self.trials[key].runs]
            remaining = min(deadlines[key] for key in pending) - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"timed out waiting for fixture results: {list(rows)}")
            try:
                row = self.results.get(timeout=min(remaining, 0.5))
            except Empty:
                continue
            if isinstance(row, VisibilityMismatch):
                if self.trials[row.trial_id].cancelled:
                    continue
                original = current.get(row.trial_id)
                if original is None or retry is None:
                    raise RuntimeError(str(row)) from row
                if retries[original] == VISIBILITY_RETRIES:
                    raise RuntimeError(f"{self.trials[original].fixture}: {row} "
                                       f"after {VISIBILITY_RETRIES} visibility retries") from row
                retries[original] += 1
                self.trials[row.trial_id].cancelled = True
                replacement = retry(row.trial_id, row, retries[original])
                del current[row.trial_id]
                current[replacement] = original
                rows[original].clear()
                deadlines[original] = time.monotonic() + timeout
                continue
            if isinstance(row, Exception):
                raise RuntimeError(str(row)) from row
            if self.trials[row["trial"]].cancelled:
                continue
            if row["trial"] not in current:
                raise RuntimeError("result from an unexpected trial")
            rows[current[row["trial"]]].append(row)
        return rows


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, body, content_type="text/plain"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/idle":
            self.respond(200, "<title>Companion spike — leave these tabs alone</title>", "text/html")
            return
        trial_id = parse_qs(url.query).get("trial", [""])[0]
        trial = self.server.trials.get(trial_id)
        if trial is None:
            self.respond(404, "unknown trial")
        elif url.path == "/ready":
            if (trial.armed.wait(30) and not trial.cancelled
                    and not self.server.closing.is_set()):
                self.respond(200, "ready")
            else:
                self.respond(503, "trial was not armed")
        elif url.path == "/" + trial.fixture:
            self.respond(200, self.server.page_html(trial_id), "text/html")
        else:
            self.respond(404, "unknown page")

    def do_POST(self):
        if self.path not in ("/results", "/visibility"):
            self.respond(404, "unknown endpoint")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65_536:
                raise ValueError("invalid result length")
            row = json.loads(self.rfile.read(length))
            if self.path == "/visibility":
                self.server.check_visibility(row)
            else:
                self.server.accept_result(row)
        except CancelledTrial as error:
            # A driver from a replaced trial can finish an in-flight request.
            # Reject it without poisoning the new trial's results queue.
            self.respond(410, str(error))
            return
        except (ValueError, TypeError) as error:
            self.server.results.put(error)
            self.respond(400, str(error))
            return
        self.respond(200, "accepted")

    def log_message(self, *_args):
        pass


@contextmanager
def serve():
    server = SpikeServer()
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.closing.set()
        server.arm(list(server.trials))
        server.shutdown()
        server.server_close()
        thread.join()


def debug_http(base_url, path, *, method="GET", as_json=False):
    # Only Chromium's HTTP target endpoints; never a WebSocket/CDP attachment.
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(base_url + path, method=method), timeout=5) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if as_json else body


def debugging_url(port, process, holder_url):
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Chromium exited before startup, status {process.returncode}")
        try:
            pages = debug_http(base_url, "/json/list", as_json=True)
            for page in pages:
                if page["type"] == "page" and page["url"] == holder_url:
                    return base_url, page["id"]
        except (URLError, TimeoutError) as error:
            last_error = error
        time.sleep(0.1)
    raise RuntimeError("Chromium's initial tab was not available within 20 seconds") from last_error


def measure(server, debug_url, holder, process, names, *, visibility, delay=0):
    targets, trial_targets = set(), {}
    active = holder

    def open_tab(url, *, foreground=False):
        nonlocal active
        target = debug_http(debug_url, "/json/new?" + quote(url, safe=""),
                            method="PUT", as_json=True)["id"]
        targets.add(target)
        if foreground:
            active = target
        # /json/new can activate its new tab. Restore the intended active tab
        # after EVERY opening, including idle slots and replacement trials.
        debug_http(debug_url, "/json/activate/" + active)
        return target

    def new_trial(name):
        trial, url = server.new_trial(name, visibility, delay=delay, hidden_ms=delay * 1000)
        trial_targets[trial] = open_tab(url, foreground=visibility == "visible")
        return trial

    def retry(trial, error, number):
        name = server.trials[trial].fixture
        print(f"{name}: {error}; visibility retry {number}/{VISIBILITY_RETRIES} "
              f"(restart {delay} s wait)", flush=True)
        debug_http(debug_url, "/json/activate/" + active)
        target = trial_targets.pop(trial)
        debug_http(debug_url, "/json/close/" + target)
        targets.remove(target)
        replacement = new_trial(name)
        server.arm([replacement])
        return replacement

    trials = [new_trial(name) for name in names]
    # Reassert the foreground after the last /json/new, before /ready lets
    # the driver verify visibility and start its scheduled wait. A lone
    # hidden fixture needs only the holder in front, not an extra idle slot.
    debug_http(debug_url, "/json/activate/" + active)
    server.arm(trials)
    rows = server.collect(trials, timeout=delay + 600, process=process, retry=retry)
    for trial in trial_targets:
        server.trials[trial].cancelled = True
    for target in targets:
        debug_http(debug_url, "/json/close/" + target)
    # If measurement fails, main's finally cleans up the entire browser.
    return {name: rows[trial] for name, trial in zip(names, trials, strict=True)}


def summarize(rows):
    successful_ms = [row["ms"] for row in rows if row["committed"]]
    return {
        "successes": len(successful_ms), "attempts": len(rows),
        "median_ms": statistics.median(successful_ms) if successful_ms else None,
        "failures": dict(Counter(row["error"] for row in rows if not row["committed"])),
    }


def print_report(cases):
    print(f"\n{'fixture':24s} {'case':26s} {'successes/attempts':>18s} "
          f"{'median success ms':>18s}  failure counts")
    for case, fixtures in cases.items():
        for name, rows in fixtures.items():
            result = summarize(rows)
            ratio = f"{result['successes']}/{result['attempts']}"
            median = "N/A" if result["median_ms"] is None else f"{result['median_ms']:.1f}"
            failures = "; ".join(f"{count}x {reason}"
                                 for reason, count in sorted(result["failures"].items())) or "none"
            print(f"{name:24s} {case:26s} {ratio:>18s} {median:>18s}  {failures}")
    for case, fixtures in cases.items():
        for name, rows in fixtures.items():
            for row in rows:
                if not row["committed"]:
                    raf = "yes" if row["raf_within_1s"] else "no"
                    print(f"failure: {case} | {name} | attempt {row['run'] + 1}/{len(rows)} | "
                          f"step={row['failure_step']} | rAF within 1 s={raf} | {row['error']}")
    sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="wait 10 seconds instead of 370")
    args = parser.parse_args()
    wait = 10 if args.quick else 370
    delayed = "background 10 s (quick)" if args.quick else "background after 6 min"
    names = list(FILLS)
    cases = {case: {} for case in ("front", "background (immediate)", delayed)}

    # Obtain the installed binary, then RELEASE Playwright before launching.
    # Even connect_over_cdp would force measured pages to report 'visible'.
    with sync_playwright() as p:
        executable = p.chromium.executable_path
    with TemporaryDirectory(prefix="companion-spike-") as profile, serve() as server:
        # Chromium's content/child/runtime_features.cc makes debug port 0
        # enable AutomationControlled. Pick an explicit free port instead.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        holder_url = server.base_url + "/idle"
        process = subprocess.Popen([
            executable, "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            # A covered window (another app in front, on macOS) hides EVERY tab in it;
            # ignore that here so "front" means the active tab of the run window. The
            # inactive tabs are still hidden, which is what this spike measures.
            "--disable-backgrounding-occluded-windows",
            holder_url,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            debug_url, holder = debugging_url(port, process, holder_url)
            print(f"{RUNS} fresh fill attempts per fixture and case; median of successes (ms).",
                  flush=True)
            print("The rAF probe runs before each fill and is excluded from its timing.", flush=True)
            print("Leave the measurement window and its tabs alone while this runs.", flush=True)
            for name in names:
                cases["front"].update(measure(
                    server, debug_url, holder, process, [name], visibility="visible",
                ))
            print_report({"front": cases["front"]})
            for case, delay in (("background (immediate)", 0), (delayed, wait)):
                # Two tabs can age/fill together behind the holder. The final
                # fixture uses one trial slot and one idle slot in the same window.
                for group in (names[:2], names[2:]):
                    print(f"{case}: {', '.join(group)} (start delay {delay} s)...", flush=True)
                    samples = measure(server, debug_url, holder, process, group,
                                      visibility="hidden", delay=delay)
                    cases[case].update(samples)
                print_report({case: cases[case]})
            if args.quick:
                print("Quick smoke test only; the six-minute throttling case was not measured.")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
