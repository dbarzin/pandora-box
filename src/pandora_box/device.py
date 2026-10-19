"""USB device description, mounting and unmounting."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pandora_box.logs import get_logger, log_event

logger = get_logger("device")

# Kernel driver used for each filesystem type reported by udev
_FS_DRIVERS = {"vfat": "vfat", "exfat": "exfat", "ntfs": "ntfs3", "ext4": "ext4"}
# Filesystems without Unix permissions: files are owned by the scanning process
_NO_UNIX_PERMISSIONS = {"vfat", "exfat", "ntfs"}
# Absolute paths: commands are never looked up in PATH
MOUNT = "/usr/bin/mount"
BLOCKDEV = "/usr/sbin/blockdev"
UMOUNT = "/usr/bin/umount"
EJECT = "/usr/bin/eject"
SUDO = "/usr/bin/sudo"
UDISKSCTL = "/usr/bin/udisksctl"


class DeviceError(Exception):
    """Raised when a device cannot be mounted or unmounted."""


@dataclass(frozen=True)
class UsbDevice:
    """A USB key: one filesystem, or the disk of a key with several partitions."""

    node: str
    fs_type: str = ""
    label: str = ""
    model: str = ""
    serial: str = ""
    vendor: str = ""
    # Filesystems of a key with several partitions, in their order; empty:
    # the key is the filesystem of node
    partitions: tuple[UsbDevice, ...] = ()
    # Partitions that cannot be scanned (encrypted, unknown content): node and
    # reason
    unscannable: tuple[tuple[str, str], ...] = ()

    @property
    def volumes(self) -> tuple[UsbDevice, ...]:
        """The filesystems to scan."""
        return self.partitions or (self,)

    @property
    def multiple(self) -> bool:
        """Several partitions: each file belongs to one of them."""
        return bool(self.partitions or self.unscannable)

    @property
    def name(self) -> str:
        """Kernel name of the node: sdb1."""
        return Path(self.node).name

    @classmethod
    def from_udev(cls, properties: Mapping[str, Any], node: str) -> UsbDevice:
        def prop(name: str) -> str:
            return str(properties.get(name) or "")

        return cls(
            node=node,
            fs_type=prop("ID_FS_TYPE"),
            label=prop("ID_FS_LABEL"),
            model=prop("ID_MODEL"),
            serial=prop("ID_SERIAL_SHORT"),
            vendor=prop("ID_VENDOR"),
        )


def mount_options(
    fs_type: str, read_only: bool, uid: int, gid: int, reader_gid: int | None = None
) -> list[str]:
    """Build hardened mount options: never execute, no setuid, no device files.

    reader_gid: group that may read the files (sandboxed scan workers).
    """
    options = ["ro" if read_only else "rw", "noexec", "nosuid", "nodev"]
    if fs_type in _NO_UNIX_PERMISSIONS:
        if reader_gid is None:
            options += [f"uid={uid}", f"gid={gid}", "fmask=0177", "dmask=0077"]
        else:
            options += [f"uid={uid}", f"gid={reader_gid}", "fmask=0137", "dmask=0027"]
        options.append("utf8" if fs_type == "vfat" else "iocharset=utf8")
    return options


class Mounter:
    """Mount the inserted device on a fixed mount point."""

    def __init__(
        self,
        mount_point: Path,
        allowed_filesystems: Sequence[str],
        use_sudo: bool = False,
        timeout: float = 60.0,
        reader_gid: int | None = None,
    ) -> None:
        self.mount_point = mount_point
        self.allowed_filesystems = tuple(allowed_filesystems)
        self.use_sudo = use_sudo
        self.timeout = timeout
        self.reader_gid = reader_gid
        self.device: UsbDevice | None = None

    def mount(self, device: UsbDevice, read_only: bool = True) -> None:
        if device.fs_type not in self.allowed_filesystems:
            raise DeviceError(f"filesystem not allowed: {device.fs_type or 'unknown'}")
        if not self.mount_point.is_dir():
            raise DeviceError(f"mount point does not exist: {self.mount_point}")
        if self.is_mounted():
            raise DeviceError(f"mount point already in use: {self.mount_point}")
        options = mount_options(
            device.fs_type, read_only, os.getuid(), os.getgid(), self.reader_gid
        )
        # The block device itself is read-only while it is scanned: the
        # kernel writes nothing to it, not even an ext4 journal replay. An
        # ext4 filesystem whose journal needs a replay is then not mounted
        # (not verified): mounted without it (noload), its files would not be
        # those a computer sees once it has replayed the journal.
        self._run(BLOCKDEV, "--setro" if read_only else "--setrw", device.node)
        try:
            self._run(
                MOUNT,
                "-t",
                _FS_DRIVERS[device.fs_type],
                "-o",
                ",".join(options),
                device.node,
                str(self.mount_point),
            )
        except DeviceError as ex:
            if read_only and device.fs_type == "ext4" and "read-only" in str(ex):
                raise DeviceError(
                    "ext4 journal to replay (not cleanly unmounted): eject the device"
                    " properly from the computer, then scan it again"
                ) from ex
            raise
        self.device = device
        log_event(logger, "device_mounted", node=device.node, options=",".join(options))

    def unmount(self) -> None:
        if not self.is_mounted():
            self.device = None
            return
        self._run(UMOUNT, str(self.mount_point))
        log_event(logger, "device_unmounted", mount_point=str(self.mount_point))
        self.device = None

    def is_mounted(self) -> bool:
        return os.path.ismount(self.mount_point)

    def device_present(self, device: UsbDevice) -> bool:
        """Check that the device is still plugged in."""
        return Path(device.node).exists()

    def eject(self, device: UsbDevice) -> None:
        """Eject an unmounted device, so that it can be removed safely."""
        if not self.device_present(device):
            return
        os.sync()
        self._run(EJECT, device.node)
        log_event(logger, "device_ejected", node=device.node)

    def _run(self, *command: str) -> None:
        argv = [SUDO, "-n", *command] if self.use_sudo else list(command)
        name = Path(command[0]).name
        try:
            subprocess.run(  # noqa: S603  (fixed command, no shell)
                argv, capture_output=True, text=True, check=True, timeout=self.timeout
            )
        except subprocess.CalledProcessError as ex:
            raise DeviceError(f"{name} failed: {ex.stderr.strip() or ex.returncode}") from ex
        except subprocess.TimeoutExpired as ex:
            raise DeviceError(f"{name} timed out") from ex
        except FileNotFoundError as ex:
            raise DeviceError(f"command not found: {argv[0]}") from ex


MOUNTS = Path("/proc/self/mounts")


def find_mount(node: str, mounts: Path = MOUNTS) -> tuple[Path, list[str]] | None:
    """Mount point and options of a device node, from /proc/self/mounts."""
    try:
        lines = mounts.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) >= 4 and fields[0] == node:
            return Path(_unescape_mount(fields[1])), fields[3].split(",")
    return None


def _unescape_mount(value: str) -> str:
    """Decode the octal escapes of /proc/self/mounts (\\040 for a space...)."""
    data = os.fsencode(value)
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i : i + 1] == b"\\" and data[i + 1 : i + 4].isdigit():
            out.append(int(data[i + 1 : i + 4], 8))
            i += 4
        else:
            out.append(data[i])
            i += 1
    return os.fsdecode(bytes(out))


class SystemMountWatcher(Mounter):
    """Use the mount made by the operating system (desktop automount).

    DEVELOPMENT ONLY (device.auto_mount): the system mounts the device with
    its own options, usually read-write and possibly without noexec, so the
    hardened read-only mount of the kiosk is lost. At insertion, we wait for
    the system mount and use its mount point. Later unmounts and mounts go
    through udisks (udisksctl), which a desktop user may run without root.
    """

    def __init__(
        self,
        allowed_filesystems: Sequence[str],
        wait: float = 15.0,
        mounts: Path = MOUNTS,
        poll: float = 0.5,
    ) -> None:
        super().__init__(Path("/nonexistent"), allowed_filesystems)
        self.wait = wait
        self.mounts = mounts
        self.poll = poll
        self.options: list[str] = []
        # Device node unmounted by us: the system will not mount it again
        self._released: str | None = None

    def mount(self, device: UsbDevice, read_only: bool = True) -> None:
        if device.fs_type not in self.allowed_filesystems:
            raise DeviceError(f"filesystem not allowed: {device.fs_type or 'unknown'}")
        if device.node == self._released and find_mount(device.node, self.mounts) is None:
            self._run(UDISKSCTL, "mount", "--no-user-interaction", "-b", device.node)
        self._released = None
        deadline = time.monotonic() + self.wait
        while (found := find_mount(device.node, self.mounts)) is None:
            if time.monotonic() >= deadline:
                raise DeviceError(f"not mounted by the system after {self.wait:g}s")
            time.sleep(self.poll)
        mount_point, options = found
        if not read_only and "rw" not in options:
            raise DeviceError("device mounted read-only by the system")
        self.mount_point, self.options = mount_point, options
        self.device = device
        log_event(
            logger,
            "device_automounted",
            logging.WARNING,
            node=device.node,
            mount_point=str(self.mount_point),
            options=",".join(self.options),
        )

    def unmount(self) -> None:
        device = self.device
        if device is not None and find_mount(device.node, self.mounts) is not None:
            self._run(UDISKSCTL, "unmount", "--no-user-interaction", "-b", device.node)
            self._released = device.node
            log_event(logger, "device_unmounted", mount_point=str(self.mount_point))
        self.device = None
        self.options = []

    def is_mounted(self) -> bool:
        return self.device is not None and find_mount(self.device.node, self.mounts) is not None

    def eject(self, device: UsbDevice) -> None:
        """Power off the drive through udisks, so that it can be removed safely."""
        if not self.device_present(device):
            return
        os.sync()
        self._run(UDISKSCTL, "power-off", "--no-user-interaction", "-b", device.node)
        self._released = None
        log_event(logger, "device_ejected", node=device.node)
