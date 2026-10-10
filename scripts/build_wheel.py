#!/usr/bin/env python3
"""Build the `maestro-career-studio` wheel: backend code + the Next standalone web app.

    python3 scripts/build_wheel.py [--version X] [--skip-frontend | --web-from WHEEL]
                                   [--no-install] [--out dist]

Stdlib only, so it runs unchanged on macOS, Linux and Windows CI. The wheel is the
`backend/` project (R1/R2 of docs/plans/2026-10-10-desktop-step3-uv-install.md) plus
`maestro_cs/web/` (the Next standalone bundle) and `maestro_cs/data/example.json`.

The backend is staged in a temp folder from `git ls-files`, so the working tree is never
edited and an untracked file (a stray `.env`, local data) cannot reach the wheel from there.
The web bundle is not tracked: Next copies `frontend/.env*` into it, so the staged copy drops
every `.env` / `.env.<name>` file (not `.env.example`), and check_wheel.py refuses any left.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

if sys.version_info < (3, 12):
    raise SystemExit("build_wheel.py needs Python 3.12 or newer (this is %d.%d)." % sys.version_info[:2])

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
WEB_PREFIX = "maestro_cs/web/"

# Not shipped: tests, and the host-only scripts (the Docker operator CLI stays in the image).
EXCLUDED_PREFIXES = ("backend/tests/", "backend/mcp_server/tests/", "backend/scripts/")

_VERSION_LINE = re.compile(r'^(version\s*=\s*)"[^"]*"', re.MULTILINE)


def staged_paths(tracked: list[str]) -> list[str]:
    """The git-tracked `backend/...` paths that belong in the wheel's source tree."""
    return [p for p in tracked if p.startswith("backend/") and not p.startswith(EXCLUDED_PREFIXES)]


def rewrite_version(pyproject_text: str, version: str) -> str:
    """Set `[project] version`. Only the first `version = ` line is touched (it is in [project])."""
    rewritten, count = _VERSION_LINE.subn(lambda m: f'{m.group(1)}"{version}"', pyproject_text, 1)
    if count != 1:
        raise ValueError("pyproject.toml has no version line to rewrite.")
    return rewritten


def project_version(pyproject_text: str) -> str:
    import tomllib  # noqa: PLC0415  (3.11+; the version guard above has already passed)

    return str(tomllib.loads(pyproject_text)["project"]["version"])


def die(message: str) -> None:
    print(f"build_wheel: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(argv: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(argv), flush=True)
    done = subprocess.run(argv, cwd=cwd, env=env, check=False)
    if done.returncode != 0:
        die(f"`{argv[0]} {argv[1] if len(argv) > 1 else ''}` failed with exit code {done.returncode}.")


def git_tracked() -> list[str]:
    done = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "backend"],
                          capture_output=True, check=False)
    if done.returncode != 0:
        die("`git ls-files` failed; run this from a git checkout.")
    return [p for p in done.stdout.decode("utf-8").split("\0") if p]


def build_frontend(version: str, install: bool) -> None:
    npm = shutil.which("npm") or die("npm is not on PATH; install Node 24, or use --skip-frontend.")
    env = {**os.environ, "NEXT_PUBLIC_APP_VERSION": version, "NEXT_TELEMETRY_DISABLED": "1"}
    if install:
        run([npm, "ci"], FRONTEND, env)
    run([npm, "run", "build"], FRONTEND, env)


def is_env_file(name: str) -> bool:
    return name == ".env" or (name.startswith(".env.") and name != ".env.example")


def drop_env_files(root: Path) -> None:
    for path in root.rglob(".env*"):
        if path.is_file() and is_env_file(path.name):
            print(f"dropping {path.relative_to(root)} from the web bundle")
            path.unlink()


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=True)


def stage_web_from_build(stage_pkg: Path) -> None:
    standalone = FRONTEND / ".next" / "standalone"
    if not (standalone / "server.js").is_file():
        die("frontend/.next/standalone/server.js is missing; build the frontend first, or give "
            "--web-from an existing wheel.")
    web = stage_pkg / "web"
    copy_tree(standalone, web)
    copy_tree(FRONTEND / ".next" / "static", web / ".next" / "static")
    copy_tree(FRONTEND / "public", web / "public")
    drop_env_files(web)


def stage_web_from_wheel(wheel: Path, stage_pkg: Path) -> None:
    if not wheel.is_file():
        die(f"{wheel} is not a file.")
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if n.startswith(WEB_PREFIX) and not n.endswith("/")]
        if not any(n == WEB_PREFIX + "server.js" for n in names):
            die(f"{wheel.name} has no {WEB_PREFIX}server.js to reuse.")
        target_root = (stage_pkg / "web").resolve()
        for name in names:
            target = (stage_pkg / name.removeprefix("maestro_cs/")).resolve()
            if target_root not in target.parents:  # a hostile member name must not escape
                die(f"{wheel.name} has a member outside {WEB_PREFIX}: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if is_env_file(target.name):
                continue
            with archive.open(name) as source, open(target, "wb") as sink:
                shutil.copyfileobj(source, sink)


def stage_backend(stage: Path, version: str | None) -> Path:
    for rel in staged_paths(git_tracked()):
        src = ROOT / rel
        if not src.is_file():
            continue  # a tracked symlink or a submodule entry
        dst = stage / rel.removeprefix("backend/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    pyproject = stage / "pyproject.toml"
    if version is not None:
        text = pyproject.read_text(encoding="utf-8")
        if project_version(text) != version:
            pyproject.write_text(rewrite_version(text, version), encoding="utf-8")
    return stage


def build_wheel(stage: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    if uv:
        run([uv, "build", "--wheel", "--out-dir", str(out)], stage)
    else:
        run([sys.executable, "-m", "build", "--wheel", "--outdir", str(out)], stage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", help="wheel version (default: backend/pyproject.toml's)")
    web = parser.add_mutually_exclusive_group()
    web.add_argument("--skip-frontend", action="store_true",
                     help="don't build the frontend; use the existing frontend/.next output")
    web.add_argument("--web-from", metavar="WHEEL", type=Path,
                     help="copy maestro_cs/web/ out of this wheel instead of building the frontend")
    parser.add_argument("--no-install", action="store_true",
                        help="build the frontend without `npm ci` (node_modules already installed)")
    parser.add_argument("--out", type=Path, default=Path("dist"), help="output folder (default: dist)")
    args = parser.parse_args(argv)

    out = args.out.resolve()
    web_from = args.web_from.resolve() if args.web_from else None
    pyproject_text = (BACKEND / "pyproject.toml").read_text(encoding="utf-8")
    version = args.version or project_version(pyproject_text)
    before = set(out.glob("*.whl")) if out.is_dir() else set()

    if not (args.skip_frontend or web_from):
        build_frontend(version, install=not args.no_install)

    with tempfile.TemporaryDirectory(prefix="maestro-wheel-", ignore_cleanup_errors=True) as tmp:
        stage = Path(tmp) / "backend"
        stage_backend(stage, args.version)
        if web_from:
            stage_web_from_wheel(web_from, stage / "maestro_cs")
        else:
            stage_web_from_build(stage / "maestro_cs")
        example = ROOT / "base_resumes" / "example.json"
        (stage / "maestro_cs" / "data").mkdir(parents=True, exist_ok=True)
        shutil.copy2(example, stage / "maestro_cs" / "data" / "example.json")
        build_wheel(stage, out)

    built = sorted(set(out.glob("*.whl")) - before) or sorted(out.glob(f"*-{version}-*.whl"))
    if len(built) != 1:
        die(f"expected one new wheel in {out}, found {[p.name for p in built]}.")
    wheel = built[0]
    check = ROOT / "scripts" / "check_wheel.py"
    run([sys.executable, str(check), str(wheel), "--expect-version", version], ROOT)
    print(f"{wheel} ({wheel.stat().st_size / 1_000_000:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
