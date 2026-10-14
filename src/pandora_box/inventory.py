"""Inventory of the files of a mounted device, and safe opening of those files.

The device is hostile: symbolic links are never followed, the walk never leaves
the mount point (each path component is opened relative to its parent with
O_NOFOLLOW), and special files are listed but never opened.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from pandora_box.logs import get_logger, log_event
from pandora_box.text import escape

logger = get_logger("inventory")

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# O_NONBLOCK: never block on a FIFO swapped in after the inventory
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOCTTY

# Skip reasons
SYMLINK = "symbolic link (not followed)"
SPECIAL_FILE = "special file (FIFO, socket or device)"
TOO_BIG = "file larger than limits.max_file_size"
TOO_DEEP = "folder deeper than limits.max_depth"
OTHER_FILESYSTEM = "other filesystem mounted on the device"
UNREADABLE = "cannot read"


class UnsafeFileError(OSError):
    """Raised when a file changed or is not what the inventory recorded."""


@dataclass(frozen=True)
class Entry:
    """A regular file to scan, as found by the inventory."""

    rel_path: str  # POSIX path relative to the mount point
    size: int
    dev: int
    ino: int


@dataclass(frozen=True)
class Skipped:
    """An entry that is not scanned.

    incomplete means that the device was not fully scanned because of it
    (a limit was exceeded or a folder could not be read).
    """

    rel_path: str
    size: int
    reason: str
    incomplete: bool


@dataclass
class Inventory:
    files: list[Entry] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    # The walk stopped early: the device was not fully inventoried
    truncated: bool = False
    incomplete_reasons: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.files)

    @property
    def complete(self) -> bool:
        return not self.incomplete_reasons


def take_inventory(root: Path, max_files: int, max_depth: int, max_file_size: int) -> Inventory:
    """List the files under root without following links.

    max_files bounds the number of entries (files, folders, links, special
    files): the walk stops beyond it. Folders deeper than max_depth are not entered.
    """
    inventory = Inventory()
    root_fd = os.open(root, _DIR_FLAGS)
    try:
        root_dev = os.fstat(root_fd).st_dev
        _Walker(inventory, root_dev, max_files, max_depth, max_file_size).walk(root_fd, "", 0)
    finally:
        os.close(root_fd)
    if inventory.truncated:
        inventory.incomplete_reasons.append(
            f"more than {max_files} files and folders (limits.max_files)"
        )
    if any(s.reason == TOO_DEEP for s in inventory.skipped):
        inventory.incomplete_reasons.append(f"folders deeper than {max_depth} (limits.max_depth)")
    if any(s.reason.startswith(UNREADABLE) for s in inventory.skipped):
        inventory.incomplete_reasons.append("unreadable folders or files")
    if any(s.reason == OTHER_FILESYSTEM for s in inventory.skipped):
        inventory.incomplete_reasons.append("other filesystems mounted on the device")
    return inventory


class _Walker:
    def __init__(
        self, inventory: Inventory, root_dev: int, max_files: int, max_depth: int, max_size: int
    ) -> None:
        self.inventory = inventory
        self.root_dev = root_dev
        self.max_files = max_files
        self.max_depth = max_depth
        self.max_size = max_size
        self.count = 0

    def skip(self, rel_path: str, size: int, reason: str, incomplete: bool) -> None:
        self.inventory.skipped.append(Skipped(rel_path, size, reason, incomplete))
        log_event(logger, "file_skipped", path=escape(rel_path), reason=reason)

    def walk(self, dir_fd: int, rel_dir: str, depth: int) -> None:
        """Walk one folder; recursion depth is bounded by max_depth."""
        subdirs: list[str] = []
        try:
            with os.scandir(dir_fd) as entries:
                for entry in entries:
                    if self.inventory.truncated:
                        return
                    rel_path = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                    if self._add(entry, rel_path):
                        subdirs.append(entry.name)
        except OSError as ex:
            self.skip(rel_dir or ".", 0, f"{UNREADABLE}: {ex.strerror}", True)
            return

        for name in subdirs:
            if self.inventory.truncated:
                return
            rel_path = f"{rel_dir}/{name}" if rel_dir else name
            if depth + 1 > self.max_depth:
                self.skip(rel_path, 0, TOO_DEEP, True)
                continue
            try:
                fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
            except OSError as ex:
                self.skip(rel_path, 0, f"{UNREADABLE}: {ex.strerror}", True)
                continue
            try:
                if os.fstat(fd).st_dev != self.root_dev:
                    self.skip(rel_path, 0, OTHER_FILESYSTEM, True)
                    continue
                self.walk(fd, rel_path, depth + 1)
            finally:
                os.close(fd)

    def _add(self, entry: os.DirEntry[str], rel_path: str) -> bool:
        """Record an entry; return True when it is a folder to walk."""
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as ex:
            self.skip(rel_path, 0, f"{UNREADABLE}: {ex.strerror}", True)
            return False
        # Folders count too: a device with millions of empty folders is bounded
        if self.count >= self.max_files:
            self.inventory.truncated = True
            return False
        self.count += 1
        if stat.S_ISDIR(st.st_mode):
            return True
        if stat.S_ISLNK(st.st_mode):
            self.skip(rel_path, st.st_size, SYMLINK, False)
        elif not stat.S_ISREG(st.st_mode):
            self.skip(rel_path, 0, SPECIAL_FILE, False)
        elif st.st_dev != self.root_dev:
            self.skip(rel_path, st.st_size, OTHER_FILESYSTEM, True)
        elif st.st_size > self.max_size:
            self.skip(rel_path, st.st_size, TOO_BIG, True)
        else:
            self.inventory.files.append(Entry(rel_path, st.st_size, st.st_dev, st.st_ino))
        return False


@contextmanager
def open_entry(root: Path, entry: Entry) -> Iterator[int]:
    """Open an inventoried file read-only and yield its descriptor.

    Each path component is opened relative to its parent without following
    links, so the file cannot be outside the mount point, and the opened file
    must be the regular file recorded by the inventory.
    """
    parts = entry.rel_path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise UnsafeFileError(f"invalid path: {escape(entry.rel_path)}")
    dir_fd = os.open(root, _DIR_FLAGS)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, _DIR_FLAGS, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = next_fd
        fd = os.open(parts[-1], _FILE_FLAGS, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise UnsafeFileError("not a regular file")
        if (st.st_dev, st.st_ino) != (entry.dev, entry.ino):
            raise UnsafeFileError("file replaced since the inventory")
        if st.st_size != entry.size:
            raise UnsafeFileError("file size changed since the inventory")
        yield fd
    finally:
        os.close(fd)
