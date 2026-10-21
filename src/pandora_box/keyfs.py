"""Files written on a key by the outbound station (airgap): the limits of its filesystem.

The files waiting on the server for a key are scanned, then written at the
root of its first filesystem. Each filesystem has its limits: files of 4 GiB
at most on vfat, names that FAT, exFAT and NTFS refuse (and Windows, which
reads the key), names of 255 characters at most, names equal but for the
case on the filesystems that ignore it. A file that cannot be written is
reported, never renamed.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pandora_box.remotes import windows_name

FAT_MAX_FILE = 4 * 1024**3 - 1
# Filesystems that ignore the case of the names, read by Windows too
CASE_INSENSITIVE = frozenset({"vfat", "exfat", "ntfs"})
MAX_NAME = 255
CHUNK = 1024 * 1024


@dataclass
class Plan:
    """The files to write on the key, and those that cannot be, with why."""

    accepted: list[tuple[str, int]] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)


def name_problem(fs_type: str, path: str) -> str:
    """Why the filesystem of the key cannot hold this path ("": it can)."""
    if fs_type in CASE_INSENSITIVE:
        reason = windows_name(path)
        if reason:
            return reason
    for part in path.split("/"):
        if fs_type in CASE_INSENSITIVE:
            length = len(part.encode("utf-16-le")) // 2
        else:
            length = len(part.encode("utf-8", "surrogateescape"))
        if length > MAX_NAME:
            return f"name longer than {MAX_NAME} characters"
    return ""


def plan(fs_type: str, files: Sequence[tuple[str, int]], free: int) -> Plan:
    """Which files fit the filesystem of the key and its free space (bytes)."""
    result = Plan()
    seen: dict[str, str] = {}
    left = free
    for path, size in files:
        reason = name_problem(fs_type, path)
        folded = path.casefold() if fs_type in CASE_INSENSITIVE else path
        if not reason and folded in seen:
            reason = f"same name as {seen[folded]} on the key (case ignored)"
        if not reason and fs_type == "vfat" and size > FAT_MAX_FILE:
            reason = "file bigger than 4 GiB, the limit of vfat"
        if not reason and size > left:
            reason = "not enough space on the key"
        if reason:
            result.rejected.append((path, reason))
            continue
        seen[folded] = path
        left -= size
        result.accepted.append((path, size))
    return result


def write_file(root: Path, path: str, source: Path, sha256: str, mtime: float) -> None:
    """Write source as path below root (a key mounted read-write), if it is the scanned file.

    The parent folders are created; a link or a file of the key in the way is
    an error. The file goes to a temporary name, then replaces any file of
    that name. Raise OSError.
    """
    parts = path.split("/")
    folder = root
    for part in parts[:-1]:
        folder = folder / part
        try:
            info = folder.lstat()
        except FileNotFoundError:
            folder.mkdir()
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise OSError(0, f"{part} on the key is not a folder")
    target = folder / parts[-1]
    if target.is_dir() and not target.is_symlink():
        raise OSError(0, "a folder of the key has this name")
    tmp = folder / f".{parts[-1]}.pandora-box-tmp"
    digest = hashlib.sha256()
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    out_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(source, flags)
    try:
        out = os.open(tmp, out_flags, 0o644)
        try:
            while chunk := os.read(fd, CHUNK):
                digest.update(chunk)
                os.write(out, chunk)
            os.fsync(out)
        finally:
            os.close(out)
    finally:
        os.close(fd)
    if digest.hexdigest() != sha256:
        tmp.unlink()
        raise OSError(0, "changed since the scan")
    os.utime(tmp, (mtime, mtime))
    tmp.replace(target)
