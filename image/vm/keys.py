"""USB key images of the virtual machine tests: one per supported filesystem,
a partitioned key and the cases the kiosk must refuse.

The images are written without root: vfat with mtools, ext4 with
mkfs.ext4 -d. No tool writes files into exfat or NTFS without mounting
them: those keys are formatted here, then filled in the machine itself
(fill_in_machine), by its kernel, while the kiosk is stopped.
"""

from __future__ import annotations

import base64
import os
import shlex
import struct
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import corpus
from machine import KEY_SIZE, Machine, MachineError, make_key

# Filled in the machine, kernel driver of each filesystem
FILLED_IN_MACHINE = {"exfat": "exfat", "ntfs": "ntfs3"}
# Clean file of the corrupted vfat key whose cluster chain is cut: unreadable
UNREADABLE = "unreadable.bin"
PARTITION_START = 1024 * 1024


def _run(*argv: str) -> None:
    subprocess.run(argv, check=True, capture_output=True)


def _blank(path: Path, size: int = KEY_SIZE) -> None:
    with path.open("wb") as image:
        image.truncate(size)


def exfat_key(path: Path) -> None:
    _blank(path)
    _run("mkfs.exfat", "-L", "USBKEY", str(path))


def ntfs_key(path: Path) -> None:
    _blank(path)
    # -F: a file, not a block device; -Q: quick format (no zeroing)
    _run("mkntfs", "-F", "-Q", "-L", "USBKEY", str(path))


def ext4_key(path: Path) -> None:
    """The corpus on ext4, private to its owner, as on the key of a Linux user.

    The kiosk opens the files and passes them to the sandboxed workers: they
    need no permission on the files.
    """
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "key"
        corpus.write_key(folder)
        for item in folder.rglob("*"):
            item.chmod(0o700 if item.is_dir() else 0o600)
        _blank(path)
        _run("mkfs.ext4", "-q", "-F", "-b", "4096", "-L", "USBKEY", "-d", str(folder), str(path))


def partitioned_key(path: Path) -> None:
    """An MBR partition table and one vfat partition holding the corpus."""
    with tempfile.TemporaryDirectory() as tmp:
        partition = Path(tmp) / "partition.img"
        make_key(partition, size=KEY_SIZE - PARTITION_START)
        _blank(path)
        subprocess.run(
            ["sfdisk", "-q", str(path)],
            input=f"start={PARTITION_START // 512}, type=c\n",
            text=True,
            check=True,
            capture_output=True,
        )
        with partition.open("rb") as source, path.open("r+b") as target:
            target.seek(PARTITION_START)
            while chunk := source.read(1024 * 1024):
                target.write(chunk)


# Clean file of the second partition of the keys with several partitions
SECOND_PARTITION_FILE = ("notes.txt", b"second partition\n")


def _with_partitions(path: Path, partitions: list[tuple[str, Path | None]]) -> None:
    """An MBR partition table with partitions of equal size: their type and
    content (a filesystem image, or None for an empty partition)."""
    size = (KEY_SIZE - PARTITION_START) // len(partitions) // (1024 * 1024) * 1024 * 1024
    _blank(path)
    table = "".join(
        f"start={(PARTITION_START + i * size) // 512}, size={size // 512}, type={kind}\n"
        for i, (kind, _) in enumerate(partitions)
    )
    subprocess.run(
        ["sfdisk", "-q", str(path)], input=table, text=True, check=True, capture_output=True
    )
    for i, (_, content) in enumerate(partitions):
        if content is None:
            continue
        with content.open("rb") as source, path.open("r+b") as target:
            target.seek(PARTITION_START + i * size)
            while chunk := source.read(1024 * 1024):
                target.write(chunk)


def two_partitions_key(path: Path) -> None:
    """The corpus on a vfat partition, a clean file on an ext4 partition."""
    size = (KEY_SIZE - PARTITION_START) // 2 // (1024 * 1024) * 1024 * 1024
    with tempfile.TemporaryDirectory() as tmp:
        first = Path(tmp) / "first.img"
        make_key(first, size=size)
        folder = Path(tmp) / "second"
        folder.mkdir()
        name, content = SECOND_PARTITION_FILE
        (folder / name).write_bytes(content)
        second = Path(tmp) / "second.img"
        _blank(second, size)
        _run("mkfs.ext4", "-q", "-F", "-L", "SECOND", "-d", str(folder), str(second))
        _with_partitions(path, [("c", first), ("83", second)])


def unknown_partition_key(path: Path) -> None:
    """A clean vfat partition and a partition without a filesystem."""
    size = (KEY_SIZE - PARTITION_START) // 2 // (1024 * 1024) * 1024 * 1024
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "first"
        folder.mkdir()
        name, content = SECOND_PARTITION_FILE
        (folder / name).write_bytes(content)
        first = Path(tmp) / "first.img"
        make_key(first, folder, size=size)
        _with_partitions(path, [("c", first), ("83", None)])


def unsupported_key(path: Path) -> None:
    """The corpus on EROFS: the kernel of the kiosk mounts it (its root
    filesystem), but it is not an allowed filesystem."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "key"
        corpus.write_key(folder)
        _run("mkfs.erofs", "-L", "USBKEY", str(path), str(folder))
    os.truncate(path, KEY_SIZE)


def corrupted_ext4_key(path: Path) -> None:
    """An ext4 key whose group descriptors are overwritten: blkid still finds
    ext4 (the superblock is intact), the kernel refuses to mount it."""
    ext4_key(path)
    with path.open("r+b") as image:
        # Block size 4096: the group descriptors are in block 1
        image.seek(4096)
        image.write(b"\xff" * 4096)


def corrupted_vfat_key(path: Path) -> None:
    """A vfat key whose file UNREADABLE has a cut cluster chain: the key is
    mounted, reading the file fails."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "readme.txt").write_bytes(corpus.KEY["readme.txt"][0])
        (folder / UNREADABLE).write_bytes(b"unreadable " * 1000)
        _blank(path)
        _run("mkfs.vfat", "-F", "16", "-n", "USBKEY", str(path))
        _run("mcopy", "-s", "-i", str(path), *[str(p) for p in sorted(folder.iterdir())], "::")
    output = subprocess.run(
        ["mshowfat", "-i", str(path), f"::/{UNREADABLE}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    # "::/unreadable.bin <3-5>": first cluster of the file
    first = int(output.split("<", 1)[1].split("-", 1)[0].rstrip(">\n"))
    with path.open("r+b") as image:
        boot = image.read(512)
        sector_size, _, reserved, fats = struct.unpack_from("<HBHB", boot, 11)
        (fat_sectors,) = struct.unpack_from("<H", boot, 22)
        for fat in range(fats):
            # FAT16: two bytes per cluster; 0 marks a free cluster
            image.seek((reserved + fat * fat_sectors) * sector_size + 2 * first)
            image.write(b"\0\0")


# Keys holding the corpus, scanned and cleaned like the vfat key
CORPUS_KEYS: dict[str, Callable[[Path], None]] = {
    "exfat": exfat_key,
    "ntfs": ntfs_key,
    "ext4": ext4_key,
    "partitioned": partitioned_key,
}


def usb_disk(machine: Machine, timeout: float = 30.0) -> str:
    """Block device of the USB key inserted in the machine."""
    deadline = time.monotonic() + timeout
    while True:
        machine.shell.run("udevadm settle")
        nodes = machine.shell.run("lsblk -d -n -o PATH,TRAN | awk '$2 == \"usb\" {print $1}'")
        if nodes.strip():
            return nodes.split()[0]
        if time.monotonic() > deadline:
            raise MachineError("the USB key does not appear in the machine")
        time.sleep(0.5)


def fill_in_machine(machine: Machine, image: Path, fs_type: str) -> None:
    """Write the corpus into a formatted key, mounted in the machine.

    The kiosk must be stopped: it would scan the key.
    """
    machine.insert_key(image)
    try:
        node = usb_disk(machine)
        target = "/run/pandora-box-test-key"
        machine.shell.run(
            f"mkdir -p {target} && mount -t {FILLED_IN_MACHINE[fs_type]} {node} {target}"
        )
        for name, (content, _) in corpus.KEY.items():
            path = shlex.quote(f"{target}/{name}")
            data = base64.b64encode(content).decode()
            machine.shell.run(f"mkdir -p $(dirname {path}) && echo {data} | base64 -d > {path}")
        machine.shell.run(f"umount {target} && rmdir {target}")
    finally:
        machine.remove_key()
