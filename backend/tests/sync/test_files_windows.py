"""Windows has no os.fchmod: a sync file write still lands intact (Task 2 of the uv-install plan)."""

import base64
import hashlib
import os

import pytest

from app.config import settings


@pytest.fixture
def applications_root(tmp_path, monkeypatch):
    root = tmp_path / "applications"
    root.mkdir()
    monkeypatch.setattr(settings, "applications_dir", root)
    return root


def test_write_succeeds_without_fchmod(applications_root, monkeypatch):
    from app.services.sync import files

    monkeypatch.delattr(os, "fchmod", raising=False)
    data = b"verified bytes"
    entry = {
        "path": "applications:X/resume.pdf",
        "sha256": hashlib.sha256(data).hexdigest(),
        "b64": base64.b64encode(data).decode("ascii"),
    }

    files.unpack([entry])

    target = applications_root / "X" / "resume.pdf"
    assert target.read_bytes() == data
    assert [p.name for p in target.parent.iterdir()] == ["resume.pdf"]  # no temp file left behind


def test_write_atomic_failure_still_removes_the_temp_without_fchmod(applications_root, monkeypatch):
    from app.services.sync import files

    monkeypatch.delattr(os, "fchmod", raising=False)
    monkeypatch.setattr(files.os, "replace", lambda *_a: (_ for _ in ()).throw(OSError("locked")))

    with pytest.raises(OSError):
        files._write_atomic(applications_root / "X" / "a.bin", b"x")

    assert list((applications_root / "X").iterdir()) == []


def test_reads_work_without_the_posix_only_open_flags(applications_root, tmp_path, monkeypatch):
    """Windows' `os` has no O_NOFOLLOW or O_NONBLOCK; packing a file and reading the sync key still work."""
    from app.services.sync import files, status

    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    monkeypatch.delattr(os, "O_NONBLOCK", raising=False)
    (applications_root / "X").mkdir()
    (applications_root / "X" / "resume.pdf").write_bytes(b"verified bytes")
    key = tmp_path / "sync-key"
    key.write_text("s3cret\n")
    monkeypatch.setattr(settings, "sync_key_file", key)

    packed, skipped = files.pack_dir_with_skips("applications", "X", max_bytes=1024)

    assert [entry["path"] for entry in packed] == ["applications:X/resume.pdf"]
    assert skipped == 0
    assert status.read_key() == "s3cret"
