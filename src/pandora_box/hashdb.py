"""Compact database of malicious SHA-256 hashes, searched by bisection with mmap.

File format (little-endian):

    offset  size  field
    0       8     magic b"USBPHDB1"
    8       4     format version (1)
    12      4     digest size (32)
    16      8     number of digests N
    24      8     creation time (Unix seconds, UTC)
    32      32    SHA-256 of the source export
    64      32*N  digests, sorted, without duplicates

A million hashes take 32 MB, shared between the scan workers through the page
cache. Build it from the abuse.ch MalwareBazaar full SHA-256 export (a zip
holding a text file, or the text file itself: one hexadecimal SHA-256 per
line, comment lines starting with '#'):

    python -m pandora_box.hashdb build full_sha256.zip malwarebazaar.sha256.bin
"""

from __future__ import annotations

import argparse
import hashlib
import io
import mmap
import os
import re
import struct
import sys
import time
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

MAGIC = b"USBPHDB1"
VERSION = 1
DIGEST_SIZE = 32
_HEADER = struct.Struct("<8sIIQQ32s")
HEADER_SIZE = _HEADER.size  # 64
_HEX_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class HashDatabaseError(Exception):
    pass


class HashDatabase:
    """Read-only, memory-mapped set of SHA-256 digests."""

    def __init__(self, path: Path, check_order: bool = True) -> None:
        self.path = path
        try:
            with path.open("rb") as f:
                size = os.fstat(f.fileno()).st_size
                if size < HEADER_SIZE:
                    raise HashDatabaseError(f"{path}: truncated header")
                self._mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except OSError as ex:
            raise HashDatabaseError(f"{path}: {ex.strerror}") from ex
        magic, version, digest_size, count, created, source = _HEADER.unpack_from(self._mm, 0)
        if magic != MAGIC:
            raise HashDatabaseError(f"{path}: not a PandoraBox hash database")
        if version != VERSION or digest_size != DIGEST_SIZE:
            raise HashDatabaseError(f"{path}: unsupported format {version}/{digest_size}")
        if size != HEADER_SIZE + count * DIGEST_SIZE:
            raise HashDatabaseError(f"{path}: size does not match {count} digests")
        self.count: int = count
        self.created = datetime.fromtimestamp(created, UTC)
        self.source_sha256: str = source.hex()
        if check_order:
            self._check_order()

    def _check_order(self) -> None:
        previous = b""
        for i in range(self.count):
            digest = self._digest(i)
            if digest <= previous:
                raise HashDatabaseError(f"{self.path}: digests not sorted at index {i}")
            previous = digest

    def _digest(self, index: int) -> bytes:
        offset = HEADER_SIZE + index * DIGEST_SIZE
        return self._mm[offset : offset + DIGEST_SIZE]

    def __contains__(self, digest: object) -> bool:
        if not isinstance(digest, bytes) or len(digest) != DIGEST_SIZE:
            return False
        lo, hi = 0, self.count
        while lo < hi:
            mid = (lo + hi) // 2
            value = self._digest(mid)
            if value == digest:
                return True
            if value < digest:
                lo = mid + 1
            else:
                hi = mid
        return False

    def contains_hex(self, sha256: str) -> bool:
        try:
            return bytes.fromhex(sha256) in self
        except ValueError:
            return False

    def close(self) -> None:
        self._mm.close()


def parse_export(lines: Iterable[str]) -> set[bytes]:
    """Parse a text export: one SHA-256 per line, '#' comments, blank lines.

    Any other line is an error: a malformed export is never silently accepted.
    """
    digests: set[bytes] = set()
    for number, raw in enumerate(lines, start=1):
        line = raw.strip().strip('"')
        if not line or line.startswith("#"):
            continue
        if not _HEX_SHA256.match(line):
            raise HashDatabaseError(f"line {number}: not a SHA-256 hash: {line[:80]!r}")
        digests.add(bytes.fromhex(line))
    return digests


def read_export(path: Path) -> tuple[set[bytes], str]:
    """Read an export (text file, or zip holding one text file).

    Return the digests and the SHA-256 of the input file.
    """
    data = path.read_bytes()
    source_sha256 = hashlib.sha256(data).hexdigest()
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if len(members) != 1:
                raise HashDatabaseError(f"{path}: expected one file in the zip archive")
            data = archive.read(members[0])
    return parse_export(data.decode("ascii").splitlines()), source_sha256


def write_database(
    digests: Iterable[bytes], output: Path, source_sha256: str = "", created: int | None = None
) -> int:
    """Write a database atomically; return the number of digests."""
    ordered = sorted(set(digests))
    if any(len(d) != DIGEST_SIZE for d in ordered):
        raise HashDatabaseError("digests must be 32 bytes")
    source = bytes.fromhex(source_sha256) if source_sha256 else bytes(32)
    header = _HEADER.pack(
        MAGIC,
        VERSION,
        DIGEST_SIZE,
        len(ordered),
        int(time.time()) if created is None else created,
        source,
    )
    tmp = output.with_name(f".{output.name}.tmp")
    with tmp.open("wb") as f:
        f.write(header)
        f.write(b"".join(ordered))
        f.flush()
        os.fsync(f.fileno())
    tmp.chmod(0o644)
    tmp.replace(output)
    return len(ordered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pandora_box.hashdb", description="MalwareBazaar hash database tool"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="convert a SHA-256 export into a database")
    build.add_argument("input", type=Path, help="text export or zip archive")
    build.add_argument("output", type=Path, help="database file to write")
    info = commands.add_parser("info", help="describe a database")
    info.add_argument("database", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            digests, source = read_export(args.input)
            count = write_database(digests, args.output, source)
            print(f"{args.output}: {count} hashes")
        else:
            db = HashDatabase(args.database)
            print(f"{args.database}: {db.count} hashes, created {db.created.isoformat()}")
            print(f"source sha256: {db.source_sha256}")
    except (HashDatabaseError, OSError, UnicodeDecodeError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
