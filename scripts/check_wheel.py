#!/usr/bin/env python3
"""Fail unless a built wheel has exactly what a uv install needs and nothing it must not ship.

    python3 scripts/check_wheel.py WHEEL [--expect-version X]

Stdlib only. Exit 1 lists every problem, not just the first.
"""
from __future__ import annotations

import argparse
import configparser
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

if sys.version_info < (3, 12):
    raise SystemExit("check_wheel.py needs Python 3.12 or newer (this is %d.%d)." % sys.version_info[:2])

MAX_BYTES = 30 * 1024 * 1024
DIST_NAME = "maestro-career-studio"
FILENAME = re.compile(r"^maestro_career_studio-(?P<version>[^-]+)-py3-none-any\.whl$")
SCRIPTS = ("maestro", "maestro-career-studio-mcp")

# A trailing "/" means "at least one member below this folder".
# "*.dist-info/" matches a top-level dist-info directory at any wheel version.
REQUIRED = (
    "app/main.py",
    "app/templates/LICENSE-resume-templates.txt",
    "*.dist-info/licenses/LICENSE",
    "*.dist-info/licenses/NOTICE",
    "*.dist-info/licenses/THIRD_PARTY_NOTICES.md",
    "mcp_server/http.py",
    "migrations/env.py",
    "migrations/script.py.mako",
    "migrations/prompt_defaults.lock.json",
    "maestro_cs/cli.py",
    "maestro_cs/maestro.env.template",
    "maestro_cs/data/example.json",
    "maestro_cs/web/server.js",
    "maestro_cs/web/.next/BUILD_ID",
    "maestro_cs/web/.next/static/",
    "maestro_cs/web/public/",
    "app/assets/fonts/xcharter/",
    "app/prompts/",
)
FORBIDDEN_SUFFIXES = (".pyc", ".node", ".dylib", ".dll", ".exe")
_SHARED_OBJECT = re.compile(r"\.so(\.\d+)*$")


def is_env_file(base: str) -> bool:
    """`.env` and `.env.<anything>` (`.env.production`), but not the `.env.example` template."""
    return base == ".env" or (base.startswith(".env.") and base != ".env.example")


def _forbidden(name: str) -> str | None:
    parts = PurePosixPath(name).parts
    base = parts[-1] if parts else ""
    if "tests" in parts:
        return "a tests/ folder"
    if base.endswith(FORBIDDEN_SUFFIXES) or _SHARED_OBJECT.search(base):
        return "a compiled or bytecode file"
    if is_env_file(base):
        return "an .env file"
    for first, second in (("node_modules", "sharp"), ("node_modules", "@img")):
        if any(a == first and b == second for a, b in zip(parts, parts[1:])):
            return f"{first}/{second}, which the build must exclude"
    if "base_resumes" in parts and base != "example.json":
        return "a base resume other than the demo"
    return None


def name_problems(names: list[str]) -> list[str]:
    """The required/forbidden member rules, on a list of archive member names."""
    members = [n for n in names if not n.endswith("/")]
    problems = []
    for required in REQUIRED:
        if required.startswith("*.dist-info/"):
            suffix = required.removeprefix("*")
            found = any(re.fullmatch(r"[^/]+" + re.escape(suffix), n) for n in members)
        else:
            found = any(n.startswith(required) for n in members) if required.endswith("/") \
                else required in members
        if not found:
            problems.append(f"missing {required}")
    if not any(re.fullmatch(r"migrations/versions/[^/]+\.py", n) for n in members):
        problems.append("missing migrations/versions/*.py")
    for name in members:
        why = _forbidden(name)
        if why:
            problems.append(f"forbidden member {name}: {why}")
    return problems


def _metadata_field(text: str, field: str) -> str | None:
    match = re.search(rf"^{field}: (.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def check(wheel: Path, expect_version: str | None = None) -> list[str]:
    problems = []
    match = FILENAME.match(wheel.name)
    if not match:
        problems.append(f"file name {wheel.name} is not maestro_career_studio-<version>-py3-none-any.whl")
    size = wheel.stat().st_size
    if size > MAX_BYTES:
        problems.append(f"size {size / 1_000_000:.1f} MB is over the {MAX_BYTES // (1024 * 1024)} MB limit")
    try:
        archive = zipfile.ZipFile(wheel)
    except (zipfile.BadZipFile, OSError) as exc:
        return [*problems, f"cannot open the wheel: {type(exc).__name__}: {exc}"]
    with archive:
        names = archive.namelist()
        problems += name_problems(names)
        metadata = [n for n in names if re.fullmatch(r"[^/]+\.dist-info/METADATA", n)]
        entry = [n for n in names if re.fullmatch(r"[^/]+\.dist-info/entry_points.txt", n)]
        if len(metadata) != 1:
            problems.append("expected one dist-info/METADATA")
        else:
            text = archive.read(metadata[0]).decode("utf-8", "replace")
            if normalise(_metadata_field(text, "Name") or "") != DIST_NAME:
                problems.append(f"METADATA Name is {_metadata_field(text, 'Name')!r}, not {DIST_NAME}")
            version = _metadata_field(text, "Version")
            if match and version != match["version"]:
                problems.append(f"METADATA Version {version} differs from the file name's {match['version']}")
            if expect_version and version != expect_version:
                problems.append(f"METADATA Version {version} is not the expected {expect_version}")
        if len(entry) != 1:
            problems.append("expected one dist-info/entry_points.txt")
        else:
            parser = configparser.ConfigParser(delimiters=("=",))
            parser.optionxform = str  # script names are case-sensitive
            parser.read_string(archive.read(entry[0]).decode("utf-8", "replace"))
            scripts = dict(parser["console_scripts"]) if parser.has_section("console_scripts") else {}
            problems += [f"entry_points.txt has no {s} script" for s in SCRIPTS if s not in scripts]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--expect-version")
    args = parser.parse_args(argv)
    if not args.wheel.is_file():
        print(f"check_wheel: {args.wheel} is not a file", file=sys.stderr)
        return 1
    problems = check(args.wheel, args.expect_version)
    if problems:
        print(f"{args.wheel.name} has {len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"{args.wheel.name}: ok ({args.wheel.stat().st_size / 1_000_000:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
