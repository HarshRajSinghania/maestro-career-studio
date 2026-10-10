"""`maestro run`: the foreground supervisor that owns the backend and web servers.

`maestro start` detaches this; autostart units run it directly. It stops on SIGTERM/SIGINT
(SIGBREAK on Windows) or when `run/stop.request` appears, which is how `maestro stop` asks on
every OS without a shutdown endpoint on a zero-auth API.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import psutil

from maestro_cs import procs

WINDOWS = sys.platform == "win32"


@dataclass
class ChildSpec:
    name: str
    argv: list[str]
    env: dict[str, str]
    cwd: Path
    log: Path
    health_url: str
    health_timeout: float = 120.0


def http_ok(url: str, timeout: float = 2.0) -> bool:
    # Loopback must bypass inherited proxy settings (the native lesson).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


class Supervisor:
    def __init__(self, run_dir: Path, specs: list[ChildSpec], *, ready: dict | None = None,
                 poll: float = 0.5, max_restarts: int = 3, restart_window: float = 300.0,
                 grace: float = 10.0, out: TextIO = sys.stdout) -> None:
        self.run_dir, self.specs, self.ready = run_dir, specs, ready or {}
        self.poll, self.max_restarts, self.restart_window = poll, max_restarts, restart_window
        self.grace, self.out = grace, out
        self.children: dict[str, subprocess.Popen] = {}
        self.exits: dict[str, list[float]] = {spec.name: [] for spec in specs}
        self.stopping = False
        self._previous_handlers: dict[int, object] = {}

    @property
    def stop_request(self) -> Path:
        return self.run_dir / "stop.request"

    def say(self, message: str) -> None:
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}", file=self.out, flush=True)

    def run(self) -> int:
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            for name in ("stop.request", "ready.json"):
                (self.run_dir / name).unlink(missing_ok=True)
            procs.write_record(self.run_dir / "supervisor.json", os.getpid())
            self._rotate_logs()
            self._install_signals()
            for spec in self.specs:
                if not self._start(spec):
                    return 0 if self._should_stop() else 1
            procs.write_json(self.run_dir / "ready.json", {
                **self.ready, **{spec.name: self.children[spec.name].pid for spec in self.specs},
            })
            self.say("Maestro is running.")
            return self._watch()
        finally:
            self._stop_all()
            for name in ("ready.json", "supervisor.json", "stop.request",
                         *(f"{spec.name}.json" for spec in self.specs)):
                (self.run_dir / name).unlink(missing_ok=True)
            # Last, so a second Ctrl-C during the cleanup above only sets the flag.
            self._restore_signals()

    def _should_stop(self) -> bool:
        return self.stopping or self.stop_request.exists()

    def _install_signals(self) -> None:
        def handler(signum, frame):  # noqa: ARG001
            self.stopping = True
        names = ("SIGINT", "SIGBREAK") if WINDOWS else ("SIGTERM", "SIGINT", "SIGHUP")
        for name in names:
            number = getattr(signal, name)
            self._previous_handlers[number] = signal.signal(number, handler)

    def _restore_signals(self) -> None:
        # The caller (or a test runner) gets its own Ctrl-C behaviour back once we are done.
        for number, previous in self._previous_handlers.items():
            try:
                signal.signal(number, previous)
            except (OSError, ValueError, TypeError):
                pass
        self._previous_handlers.clear()

    def _rotate_logs(self) -> None:
        # Once per run, not per spawn: an in-run restart appends, so the crash that caused it
        # stays in the same file instead of overwriting last run's backend.log.1.
        for spec in self.specs:
            if not spec.log.exists():
                continue
            try:
                os.replace(spec.log, spec.log.with_name(spec.log.name + ".1"))
            except OSError:  # Windows: another process (`maestro logs -f`) holds the file open
                self.say(f"Couldn't rotate {spec.log.name}; appending.")

    def _spawn(self, spec: ChildSpec) -> subprocess.Popen | None:
        kwargs: dict = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {
            "start_new_session": True}
        try:
            with open(spec.log, "ab") as log:  # the child keeps its own handle
                proc = subprocess.Popen(spec.argv, cwd=spec.cwd, env=spec.env,
                                        stdin=subprocess.DEVNULL, stdout=log,
                                        stderr=subprocess.STDOUT, **kwargs)
        except OSError as exc:  # e.g. node missing or not executable
            self.say(f"Cannot start {spec.name}: {spec.argv[0]} could not be run "
                     f"({exc.strerror or exc}).")
            return None
        self.children[spec.name] = proc
        try:
            procs.write_record(self.run_dir / f"{spec.name}.json", proc.pid)
        except psutil.Error:
            pass  # it already exited; _start reports the exit code
        self.say(f"Started {spec.name} (pid {proc.pid}).")
        return proc

    def _start(self, spec: ChildSpec) -> bool:
        proc = self._spawn(spec)
        if proc is None:
            return False
        deadline = time.monotonic() + spec.health_timeout
        while time.monotonic() < deadline:
            if self._should_stop():
                return False
            if proc.poll() is not None:
                self.say(f"{spec.name} exited during start with code {proc.returncode}; see {spec.log.name}.")
                return False
            if http_ok(spec.health_url):
                return True
            time.sleep(self.poll)
        self.say(f"{spec.name} did not become healthy within {spec.health_timeout:g} seconds.")
        return False

    def _may_restart(self, name: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self.exits[name] if now - t < self.restart_window] + [now]
        self.exits[name] = recent
        return len(recent) <= self.max_restarts

    def _watch(self) -> int:
        while not self._should_stop():
            time.sleep(self.poll)
            if self._should_stop():  # a stop that arrived during the sleep must not restart anything
                break
            for spec in self.specs:
                proc = self.children[spec.name]
                if proc.poll() is None:
                    continue
                if not self._may_restart(spec.name):
                    self.say(f"{spec.name} keeps exiting (code {proc.returncode}); giving up.")
                    return 1
                self.say(f"{spec.name} exited with code {proc.returncode}; restarting.")
                if not self._start(spec):
                    return 0 if self._should_stop() else 1
        self.say("Stopping on request.")
        return 0

    def _stop_child(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:  # taken now: once the child exits, its helpers are re-parented and unfindable
            helpers = psutil.Process(proc.pid).children(recursive=True)
        except psutil.Error:
            helpers = []
        try:
            if WINDOWS:
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # uvicorn and node exit cleanly on it
            else:
                os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=self.grace)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if proc.poll() is None:
            try:
                procs.kill_tree(psutil.Process(proc.pid), grace=2.0)
            except psutil.Error:
                pass
        for helper in helpers:  # whatever outlived its parent
            try:
                helper.kill()
            except psutil.Error:
                pass
        try:
            proc.wait(timeout=2.0)  # reap it so no zombie is left behind
        except subprocess.TimeoutExpired:
            pass

    def _stop_all(self) -> None:
        for spec in reversed(self.specs):  # the UI first, then the API it proxies to
            proc = self.children.get(spec.name)
            if proc is not None:
                self._stop_child(proc)
