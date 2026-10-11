"""Resident memory of this process, for /health/memory (docs/plans/2026-10-06-slim-pilot.md)."""

import sys
from pathlib import Path

try:
    import resource
except ImportError:  # Windows has no `resource`; psutil (a core dependency) reads the working set instead
    resource = None


def _psutil_rss_mb() -> float:
    import psutil

    return round(psutil.Process().memory_info().rss / (1024 * 1024), 1)


def rss_mb() -> float:
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    return peak_mb()  # macOS has no /proc; the peak is the best cheap stand-in


def peak_mb() -> float:
    if resource is None:
        return _psutil_rss_mb()  # no peak counter without `resource`; the current RSS stands in
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024, 1)


def readout() -> dict:
    rss = rss_mb()
    # ru_maxrss can lag VmRSS on Linux, so the peak is never reported below the current reading.
    return {"rss_mb": rss, "peak_mb": max(peak_mb(), rss), "platform": sys.platform}
