"""Entry point for the `maestro` command. Verbs land in later tasks; this stub prints usage."""
from __future__ import annotations

import sys

from maestro_cs import package_version

USAGE = """\
maestro {version}: Maestro Career Studio, run from a uv install.

Usage: maestro <command>

The commands (start, stop, update, connect, ...) are not available in this build yet.
"""


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    print(USAGE.format(version=package_version()), end="")
    return 0 if not args or args[0] in ("-h", "--help", "help") else 2
