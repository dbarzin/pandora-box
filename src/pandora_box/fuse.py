"""Filesystem drivers in user space, in the sandbox of the scan workers (device.fuse).

A flaw of a kernel filesystem driver, on structures crafted by a device, runs
code in the kernel, before any sandbox. NTFS, exFAT and ext4 are therefore
read by a FUSE driver (ntfs-3g, exfat-fuse, fuse2fs), which runs in the
sandbox of the scan workers (pandora_box.sandbox) with the block device as
its only device:

- the kiosk opens /dev/fuse and mounts the connection itself (mount -t
  fuse.TYPE -o fd=N): the driver needs no privilege to serve it;
- libfuse 3 drivers take the mounted connection as mount point /dev/fd/N;
  ntfs-3g carries its own FUSE library, which does not: a preloaded library
  hands it the connection (packaging/fuse/premounted.c);
- the driver runs as the sandbox user with the group of the block device;
  this module, run as its first process (python -m pandora_box.fuse), waits
  for the processes of the driver (exfat-fuse goes to the background);
- a watchdog stops a driver whose requests stay unanswered while the device
  is idle: a crashed or stopped driver makes every access fail (ENOTCONN),
  and the device is reported as not verified.

A compromised driver no longer takes over the kiosk, but it can still hide
files from the scan.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from pandora_box.logs import get_logger, log_event
from pandora_box.sandbox import Sandbox

logger = get_logger("fuse")

# Filesystems read by a FUSE driver; vfat stays in the kernel, its simplest
# and oldest driver
FUSE_FILESYSTEMS = ("exfat", "ntfs", "ext4")
FUSE_DEVICE = "/dev/fuse"
NTFS_3G = "/usr/bin/ntfs-3g"
EXFAT_FUSE = "/usr/sbin/mount.exfat-fuse"
FUSE2FS = "/usr/bin/fuse2fs"
ENV = "/usr/bin/env"
# Built from packaging/fuse/premounted.c
PREMOUNTED = "/usr/lib/pandora-box/fuse-premounted.so"
# Files of /etc the drivers read: the name of their user (ntfs-3g, exfat-fuse)
ETC_FILES = ("/etc/passwd", "/etc/group", "/etc/nsswitch.conf")
# Mount point given to ntfs-3g, which only checks it: an empty folder of the
# sandbox, writable by its user
NTFS_MOUNT_POINT = "/mnt"
CONNECTIONS = Path("/sys/fs/fuse/connections")
BLOCK_DEVICES = Path("/sys/class/block")
MOUNTINFO = Path("/proc/self/mountinfo")
# Last bytes of the output of a driver kept for the error messages
OUTPUT_LIMIT = 4096
POLL = 1.0
PR_SET_CHILD_SUBREAPER = 36
# mount(2) flags
MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8


class FuseError(Exception):
    """The driver failed, or does not answer."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


def mount_connection(fd: int, fs_type: str, node: str, mount_point: Path, read_only: bool) -> None:
    """Mount the FUSE connection fd on mount_point: noexec, nosuid, nodev.

    With the mount(2) system call: mount(8) looks at the mount point once it
    is mounted, a request that no driver serves yet. Only root, and the
    workers given a file by the kiosk, read it.
    """
    flags = MS_NOSUID | MS_NODEV | MS_NOEXEC | (MS_RDONLY if read_only else 0)
    data = f"fd={fd},rootmode=40000,user_id=0,group_id=0,allow_other"
    _libc("mount", node.encode(), os.fsencode(mount_point), f"fuse.{fs_type}".encode(), flags,
          data.encode())  # fmt: skip


def unmount_connection(mount_point: Path) -> None:
    """Unmount a FUSE connection, with the umount2(2) system call."""
    _libc("umount2", os.fsencode(mount_point), 0)


def _libc(name: str, *args: object) -> None:
    function = getattr(ctypes.CDLL(None, use_errno=True), name)
    if function(*args) != 0:
        errno = ctypes.get_errno()
        raise FuseError(f"{name}: {os.strerror(errno)}")


def driver_command(fs_type: str, node: str, fd: int, read_only: bool, gid: int) -> list[str]:
    """The command line of the driver serving the connection fd for node.

    Files are owned by root, readable by the group gid (sandboxed workers).
    """
    ro = ["ro"] if read_only else []
    permissions = [f"uid=0,gid={gid},fmask=0137,dmask=0027"]
    if fs_type == "ntfs":
        options = ",".join([*ro, "no_detach", *permissions])
        return [
            ENV,
            f"LD_PRELOAD={PREMOUNTED}",
            f"PANDORA_BOX_FUSE_FD={fd}",
            NTFS_3G,
            "-o",
            options,
            node,
            NTFS_MOUNT_POINT,
        ]
    if fs_type == "exfat":
        return [EXFAT_FUSE, "-o", ",".join([*ro, *permissions]), node, f"/dev/fd/{fd}"]
    if fs_type == "ext4":
        # Files keep their ext4 owner: the kiosk opens them as root
        return [FUSE2FS, "-f", *(["-o", "ro"] if read_only else []), node, f"/dev/fd/{fd}"]
    raise ValueError(f"no FUSE driver for {fs_type}")


def sandbox_options(node: str) -> list[str]:
    """bubblewrap options of the driver: the block device, its user name."""
    options = ["--dev-bind", node, node]
    for path in ETC_FILES:
        options += ["--ro-bind-try", path, path]
    options += ["--perms", "0777", "--dir", NTFS_MOUNT_POINT]
    return options


def connection_id(mount_point: Path, mountinfo: Path = MOUNTINFO) -> str | None:
    """Name of the FUSE connection mounted on mount_point (/sys/fs/fuse/connections).

    Read from the device number in mountinfo: a stat of the mount point would
    be a request to the driver.
    """
    try:
        lines = mountinfo.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
    except OSError:
        return None
    found = None
    for line in lines:
        fields = line.split()
        if len(fields) > 4 and unescape_mount(fields[4]) == str(mount_point):
            found = fields[2]
    if found is None:
        return None
    major, _, minor = found.partition(":")
    # The kernel names it after its own encoding of the device number
    return str((int(major) << 20) | int(minor))


def unescape_mount(value: str) -> str:
    """Decode the octal escapes of /proc/self/mounts and mountinfo (\\040 for a space...)."""
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


class FuseDriver:
    """A driver serving a FUSE connection that the kiosk mounted."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        name: str,
        node: str,
        mount_point: Path,
        timeout: float,
        connections: Path = CONNECTIONS,
        block_devices: Path = BLOCK_DEVICES,
        mountinfo: Path = MOUNTINFO,
        poll: float = POLL,
    ) -> None:
        self.process = process
        self.name = name
        self.mount_point = mount_point
        self.timeout = timeout
        self.poll = poll
        self._failure: str | None = None
        self._lock = threading.Lock()
        connection = connection_id(mount_point, mountinfo)
        self._waiting = None if connection is None else connections / connection / "waiting"
        self._io = block_devices / Path(os.path.realpath(node)).name / "stat"
        self._output = bytearray()
        self._stopping = threading.Event()
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        self._watchdog = threading.Thread(target=self._watch, daemon=True)
        self._watchdog.start()

    @property
    def output(self) -> str:
        return self._output.decode("utf-8", "replace")

    @property
    def failure(self) -> str | None:
        """Why the driver stopped serving the device; None while it serves it."""
        status = self.process.poll()
        if status is not None and not self._stopping.is_set():
            self._fail(f"stopped (exit status {status})", "fuse_driver_stopped")
        return self._failure

    def _fail(self, reason: str, event: str) -> None:
        with self._lock:
            if self._failure is not None:
                return
            self._failure = f"{self.name} {reason}"
        log_event(
            logger, event, logging.ERROR, driver=self.name, reason=reason, output=self.output[-512:]
        )

    def wait_ready(self) -> None:
        """Wait until the driver answers; raise FuseError if it failed or is stuck."""
        error: list[OSError] = []

        def probe() -> None:
            try:
                os.statvfs(self.mount_point)
            except OSError as ex:
                error.append(ex)

        thread = threading.Thread(target=probe, daemon=True)
        thread.start()
        thread.join(self.timeout)
        if thread.is_alive():
            self._kill(f"no answer within {self.timeout:g}s")
            thread.join(self.poll)
            raise FuseError(f"{self.name}: no answer within {self.timeout:g}s", self.output)
        if error:
            # The output is complete once the driver has exited
            try:
                self.process.wait(self.timeout)
            except subprocess.TimeoutExpired:
                self._kill("failed to start")
            self._reader.join(self.poll)
            raise FuseError(f"{self.name}: {self._reason(error[0])}", self.output)

    def unmounting(self) -> None:
        """The connection is about to be unmounted: the driver will exit."""
        self._stopping.set()

    def stop(self) -> None:
        """Wait for the driver to exit once unmounted: it writes its last changes."""
        self._stopping.set()
        try:
            self.process.wait(self.timeout)
        except subprocess.TimeoutExpired:
            self._kill("did not exit after the unmount")
            raise FuseError(f"{self.name} did not exit after the unmount") from None
        finally:
            self._watchdog.join(self.poll * 2)

    def _reason(self, error: OSError) -> str:
        lines = [line.strip() for line in self.output.splitlines() if line.strip()]
        if lines:
            return lines[-1]
        if self.process.returncode:
            return f"exit status {self.process.returncode}"
        return error.strerror or str(error)

    def _read_output(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        while chunk := stream.read1(OUTPUT_LIMIT):  # type: ignore[attr-defined]
            self._output += chunk
            del self._output[:-OUTPUT_LIMIT]

    def _kill(self, reason: str) -> None:
        # The reason first: once killed, the driver would be reported stopped
        self._fail(reason, "fuse_driver_killed")
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()

    def _watch(self) -> None:
        stalled_since: float | None = None
        last_io: str | None = None
        while not self._stopping.wait(self.poll):
            if self.process.poll() is not None:
                # Logged now, not when the kiosk asks
                _ = self.failure
                return
            if not self._read_int(self._waiting):
                stalled_since = None
                continue
            # Requests are waiting: stuck when the device does nothing
            # either (the driver is not waiting for a slow device)
            io = self._read_text(self._io)
            now = time.monotonic()
            if stalled_since is None or io != last_io:
                stalled_since, last_io = now, io
            elif now - stalled_since >= self.timeout:
                self._kill(f"not answering for {self.timeout:g}s")
                return

    @staticmethod
    def _read_text(path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return path.read_text(encoding="ascii")
        except (OSError, UnicodeDecodeError):
            return None

    def _read_int(self, path: Path | None) -> int:
        text = self._read_text(path)
        try:
            return int(text) if text is not None else 0
        except ValueError:
            return 0


def start_driver(
    sandbox: Sandbox,
    fs_type: str,
    node: str,
    fd: int,
    mount_point: Path,
    read_only: bool,
    gid: int,
    timeout: float,
) -> FuseDriver:
    """Start the driver of fs_type in the sandbox, serving the connection fd."""
    command = driver_command(fs_type, node, fd, read_only, gid)
    argv = sandbox.wrap(
        [sys.executable, "-m", "pandora_box.fuse", *command],
        options=sandbox_options(node),
        groups=(Path(node).stat().st_gid,),
    )
    process = subprocess.Popen(  # noqa: S603  (fixed command, no shell)
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        pass_fds=(fd,),
    )
    name = Path(command[3] if fs_type == "ntfs" else command[0]).name
    log_event(logger, "fuse_driver_started", driver=name, node=node, pid=process.pid)
    return FuseDriver(process, name, node, mount_point, timeout)


def reap(command: Sequence[str]) -> int:
    """Run command and wait for all its processes; its exit status, else theirs.

    The first process of the sandbox: bubblewrap tears the sandbox down when
    it exits, so it stays while a driver gone to the background serves the
    device.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        print("prctl: cannot wait for the background processes", file=sys.stderr)
        return 1
    first = os.spawnv(os.P_NOWAIT, command[0], list(command))  # noqa: S606  (no shell)
    status = 0
    while True:
        try:
            pid, wait_status = os.wait()
        except ChildProcessError:
            return status
        code = os.waitstatus_to_exitcode(wait_status)
        if code < 0:
            code = 128 - code
        if code and (pid == first or not status):
            status = code


if __name__ == "__main__":
    sys.exit(reap(sys.argv[1:]))
