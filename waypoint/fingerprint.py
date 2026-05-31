"""Artifact fingerprinting and resume-integrity detection.

A checkpoint records a fingerprint of each result artifact. On resume we
re-check it to answer "is this step's result still there?" — the §9
decision tree: missing -> gone, match -> intact, differ -> changed.

The authoritative identity is the git blob hash (``git hash-object``) when
the file lives in a git repo — it is git's own content identity and is
near-free. ``sha256`` is the fallback for non-git files. ``size`` + ``mtime``
are a cheap pre-check that lets us skip hashing when nothing changed.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from typing import Optional

# Result of comparing a recorded fingerprint to the file on disk.
MISSING = "missing"   # file gone -> surface to human
INTACT = "intact"     # byte-identical -> keep going
CHANGED = "changed"   # present but differs -> "go deep" / surface


def git_blob(path: str) -> Optional[str]:
    """Return the git blob SHA of ``path``'s current content, or None.

    Uses ``git hash-object`` which hashes the working-tree file content
    regardless of whether it is committed or even tracked, as long as a git
    binary is available. Returns None if git is unavailable or errors.

    Args:
        path: Filesystem path to hash.

    Returns:
        The 40-char blob SHA as a string, or None on any failure.
    """
    try:
        out = subprocess.run(
            ["git", "hash-object", "--", path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    sha = out.stdout.strip()
    return sha or None


def sha256(path: str) -> Optional[str]:
    """Return the SHA-256 of the file content, or None if unreadable.

    Args:
        path: Filesystem path to hash.

    Returns:
        Hex digest string, or None if the file cannot be read.
    """
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def fingerprint(path: str) -> dict:
    """Compute a fingerprint record for ``path``.

    Args:
        path: Filesystem path of the artifact.

    Returns:
        A dict with ``path`` and, when available, ``git_blob``, ``sha256``,
        ``size`` and ``mtime``. ``exists`` is False when the file is absent.
    """
    rec: dict = {"path": path}
    try:
        st = os.stat(path)
    except OSError:
        rec["exists"] = False
        return rec
    rec["exists"] = True
    rec["size"] = st.st_size
    rec["mtime"] = int(st.st_mtime)
    blob = git_blob(path)
    if blob:
        rec["git_blob"] = blob
    else:
        digest = sha256(path)
        if digest:
            rec["sha256"] = digest
    return rec


def verify(record: dict) -> str:
    """Compare a recorded fingerprint against the file on disk.

    Three layers, cheapest first (§9): existence, then size+mtime fast path,
    then authoritative content hash (git blob, else sha256).

    Args:
        record: A fingerprint dict previously produced by :func:`fingerprint`.

    Returns:
        One of :data:`MISSING`, :data:`INTACT`, :data:`CHANGED`.
    """
    path = record.get("path")
    if not path or not os.path.exists(path):
        return MISSING

    # Layer 2: fast stamp. If size+mtime both match the record, treat as
    # untouched without reading the file.
    try:
        st = os.stat(path)
    except OSError:
        return MISSING
    if (
        "size" in record
        and "mtime" in record
        and st.st_size == record["size"]
        and int(st.st_mtime) == record["mtime"]
    ):
        return INTACT

    # Layer 3: authoritative content identity.
    if "git_blob" in record:
        current = git_blob(path)
        if current is None:
            # Cannot recompute the same way; fall back to sha256 if present.
            if "sha256" in record:
                return INTACT if sha256(path) == record["sha256"] else CHANGED
            return CHANGED
        return INTACT if current == record["git_blob"] else CHANGED
    if "sha256" in record:
        return INTACT if sha256(path) == record["sha256"] else CHANGED

    # No content hash recorded and stamps differ -> cannot prove intact.
    return CHANGED
