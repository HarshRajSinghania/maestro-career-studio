#!/usr/bin/env python3
"""Package the Companion for Chrome Web Store upload, using only the stdlib."""

import argparse
import json
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV_FILES = {"README.md", "INTERNALS.md", "STORE_LISTING.md"}


def pack_extension(root: Path, *, first_upload: bool = False) -> Path:
    extension = root / "extension"
    manifest_path = extension / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with (root / "backend/pyproject.toml").open("rb") as source:
        backend_version = tomllib.load(source)["project"]["version"]
    version = manifest["version"]
    if version != backend_version:
        raise ValueError(
            f"Version mismatch: extension is {version}, backend is {backend_version}."
        )

    archive = root / "dist" / f"maestro-cs-companion-{version}.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(extension.rglob("*")):
            relative = path.relative_to(extension)
            if not path.is_file() or any(
                part.startswith(".") or part in {"tests", "test", "__pycache__"}
                for part in relative.parts
            ):
                continue
            if path.name in DEV_FILES or path.name.startswith("test_") or any(
                marker in path.name for marker in (".test.", ".spec.")
            ):
                continue
            if relative.as_posix() == "manifest.json" and first_upload:
                upload_manifest = {key: value for key, value in manifest.items() if key != "key"}
                content = json.dumps(upload_manifest, indent=2) + "\n"
            else:
                content = path.read_bytes()
            entry = zipfile.ZipInfo(relative.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = 0o644 << 16
            zipped.writestr(entry, content, compress_type=zipfile.ZIP_DEFLATED)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-upload", action="store_true", help="omit the key in the zip only")
    args = parser.parse_args()
    try:
        archive = pack_extension(ROOT, first_upload=args.first_upload)
    except (OSError, ValueError, KeyError) as error:
        print(f"Cannot pack extension: {error}", file=sys.stderr)
        return 1
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
