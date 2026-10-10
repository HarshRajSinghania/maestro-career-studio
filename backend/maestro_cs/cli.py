"""The `maestro` command: argument parsing and one-sentence errors.

The work lives in `maestro_cs.control`; this module only maps verbs to it and turns a
`HomeError` or `ControlError` into a sentence on stderr with exit code 1 (never a traceback).
"""
from __future__ import annotations

import argparse
import sys

from maestro_cs import control, package_version
from maestro_cs.home import HomeError

DESCRIPTION = "Maestro Career Studio, run from a uv install."
EPILOG = "Run `maestro <command> --help` for a command's options."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="maestro", description=DESCRIPTION, epilog=EPILOG)
    parser.add_argument("--version", action="version",
                        version=f"maestro-career-studio {package_version()}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    start = sub.add_parser("start", help="start Maestro in the background and open it")
    start.add_argument("--no-browser", action="store_true", help="don't open the browser")
    start.add_argument("--skip-model", action="store_true",
                       help="don't download the scoring model now")
    sub.add_parser("stop", help="stop Maestro")
    sub.add_parser("restart", help="stop, then start (doesn't open the browser)")
    status = sub.add_parser("status", help="show whether Maestro is running (exit 3 if not)")
    status.add_argument("--json", action="store_true", help="machine-readable output")
    status.add_argument("--offline", action="store_true", help="don't check for a newer release")
    logs = sub.add_parser("logs", help="show the end of a log")
    logs.add_argument("which", nargs="?", default="backend", choices=control.LOG_NAMES,
                      help="which log (default: backend)")
    logs.add_argument("-n", "--lines", type=int, default=50, help="how many lines (default 50)")
    logs.add_argument("-f", "--follow", action="store_true", help="keep printing new lines")
    sub.add_parser("open", help="open Maestro in the browser")
    sub.add_parser("run", help="run Maestro in the foreground (used by autostart)")
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    ctx = control.load_context()
    if args.command == "run":
        return control.run(ctx)
    ctl = control.Controller(ctx)
    if args.command == "start":
        return ctl.start(no_browser=args.no_browser, skip_model=args.skip_model)
    if args.command == "stop":
        return ctl.stop()
    if args.command == "restart":
        return ctl.restart()
    if args.command == "status":
        return ctl.status(as_json=args.json, offline=args.offline)
    if args.command == "logs":
        return ctl.logs(args.which, max(args.lines, 1), args.follow)
    return ctl.open()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:  # --help, --version and usage errors
        return exc.code if isinstance(exc.code, int) else 0
    if not args.command:
        parser.print_help()
        return 0
    try:
        return _dispatch(args)
    except (HomeError, control.ControlError) as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
