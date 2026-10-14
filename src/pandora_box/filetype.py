"""Real file type identification with libmagic (python-magic).

libmagic parses hostile content: it only gets the first bytes of the file as a
buffer (no file system access, no decompression) and runs in a scan worker.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import magic

# Bytes given to libmagic: enough for the usual signatures (ISO 9660 at 32 KiB)
HEAD_SIZE = 1024 * 1024


@dataclass(frozen=True)
class FileType:
    mime: str | None
    description: str | None
    error: str | None = None


class FileTypeDetector:
    """One instance per worker process: libmagic handles are not shared."""

    def __init__(self, head_size: int = HEAD_SIZE) -> None:
        self.head_size = head_size
        self._mime = magic.Magic(mime=True)
        self._description = magic.Magic()

    def identify(self, fd: int) -> FileType:
        try:
            head = os.pread(fd, self.head_size, 0)
            return FileType(self._mime.from_buffer(head), self._description.from_buffer(head))
        except (OSError, magic.MagicException) as ex:
            return FileType(None, None, str(ex))


def magic_version() -> str:
    """Version of the libmagic library, for scan reports."""
    try:
        version: int = magic.version()  # type: ignore[no-untyped-call]
    except (AttributeError, NotImplementedError):
        return ""
    return f"{version // 100}.{version % 100:02d}"
