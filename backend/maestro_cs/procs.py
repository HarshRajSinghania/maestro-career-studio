"""Process records that survive reboots honestly: a pid alone can name someone else's process."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import psutil


def write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    if os.name == "posix":
        tmp.chmod(0o600)
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_record(path: Path, pid: int, **extra: Any) -> None:
    write_json(path, {"pid": pid, "create_time": psutil.Process(pid).create_time(), **extra})


def live_process(record: dict[str, Any] | None) -> psutil.Process | None:
    if not record:
        return None
    try:
        proc = psutil.Process(int(record["pid"]))
        # Linux derives create_time from the boot time, which moves when the wall clock is stepped.
        same = abs(proc.create_time() - float(record["create_time"])) < 2.0
        if same and proc.status() != psutil.STATUS_ZOMBIE:
            return proc
    except (psutil.Error, KeyError, TypeError, ValueError):
        pass
    return None


def kill_tree(proc: psutil.Process, grace: float = 5.0) -> None:
    try:
        family = [proc, *proc.children(recursive=True)]
    except psutil.Error:
        return
    for member in family:
        try:
            member.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(family, timeout=grace)
    for member in alive:
        try:
            member.kill()
        except psutil.Error:
            pass
