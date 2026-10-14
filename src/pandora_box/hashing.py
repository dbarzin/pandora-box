"""Hash computation: SHA-256, SHA-1 and MD5 in a single streaming read."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class Hashes:
    sha256: str
    sha1: str
    md5: str
    size: int  # number of bytes actually read


def hash_fd(fd: int, chunk_size: int = CHUNK_SIZE) -> Hashes:
    """Hash the whole content of fd with fixed-size reads (pread: offset unchanged)."""
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1()  # noqa: S324  (identification only: Hashlookup uses SHA-1)
    md5 = hashlib.md5()  # noqa: S324  (identification only)
    offset = 0
    while chunk := os.pread(fd, chunk_size, offset):
        sha256.update(chunk)
        sha1.update(chunk)
        md5.update(chunk)
        offset += len(chunk)
    return Hashes(sha256.hexdigest(), sha1.hexdigest(), md5.hexdigest(), offset)
