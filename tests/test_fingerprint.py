"""Tests for artifact fingerprinting and the §9 resume-integrity verdicts."""

import os

from waypoint import fingerprint


def test_fingerprint_records_identity(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    rec = fingerprint.fingerprint(str(f))
    assert rec["exists"] is True
    assert rec["size"] == 5
    assert "mtime" in rec
    # Either a git blob or a sha256 fallback must be present.
    assert ("git_blob" in rec) or ("sha256" in rec)


def test_verify_intact_via_fast_path(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    rec = fingerprint.fingerprint(str(f))
    assert fingerprint.verify(rec) == fingerprint.INTACT


def test_verify_missing(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    rec = fingerprint.fingerprint(str(f))
    f.unlink()
    assert fingerprint.verify(rec) == fingerprint.MISSING


def test_verify_changed(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    rec = fingerprint.fingerprint(str(f))
    # Different length defeats the size+mtime fast path; content hash differs.
    f.write_text("a much longer different content")
    os.utime(str(f), (rec["mtime"] + 5, rec["mtime"] + 5))
    assert fingerprint.verify(rec) == fingerprint.CHANGED


def test_verify_missing_on_empty_record():
    assert fingerprint.verify({}) == fingerprint.MISSING
