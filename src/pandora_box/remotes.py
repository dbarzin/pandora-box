"""Mirror of a local folder to a remote server: rsync over SSH, SFTP, SMB (airgap).

The remote folder becomes a copy of the local one, in one direction: new and
changed files (size, modification time) are copied, files and folders that
are not in the local folder any more are deleted. Names listed in protect
(the reports of the kiosk) are left alone at the top of the remote folder.

rsync runs the OpenSSH client and needs rsync on the server. SFTP (paramiko)
and SMB (smbprotocol, SMB 3 with encryption) use the same comparison, written
here. The server is always authenticated: its SSH host key must be listed in
known_hosts; SMB relies on the account and the encryption of SMB 3.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

RSYNC = "/usr/bin/rsync"
SSH = "/usr/bin/ssh"
# Modification times differ by up to 2 seconds between filesystems (vfat)
MODIFY_WINDOW = 2
CHUNK = 1024 * 1024
# Folder of the scan reports, at the top of the folder of a key on the server
REPORTS = ".pandora-box"
# Characters a file name cannot hold on an SMB server
_SMB_FORBIDDEN = set('\\:*?"<>|') | {chr(c) for c in range(32)}

Progress = Callable[[int, int], None]


class TransferError(Exception):
    """The server cannot be reached, or refuses the transfer."""


@dataclass(frozen=True)
class Entry:
    size: int
    mtime: int
    is_dir: bool = False


@dataclass
class SyncResult:
    copied: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: int = 0
    # Files that could not be copied, and why
    errors: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Server:
    """Where to copy: protocol, server, account and folder (configuration)."""

    protocol: str
    host: str
    port: int
    user: str
    # Folder on the server (SMB: in the share)
    path: str
    share: str = ""
    domain: str = ""
    known_hosts: Path | None = None
    timeout: float = 60.0


@dataclass(frozen=True)
class Secrets:
    """Credentials of the account (systemd credentials of the service)."""

    ssh_key: Path | None = None
    password: str = ""


def scan_local(root: Path) -> dict[str, Entry]:
    """Files and folders below root, by path relative to it (links are skipped)."""
    entries: dict[str, Entry] = {}
    for folder, dirs, files in os.walk(root):
        relative = Path(folder).relative_to(root)
        for name in dirs + files:
            path = Path(folder, name)
            info = path.lstat()
            key = (relative / name).as_posix()
            if stat.S_ISDIR(info.st_mode):
                entries[key] = Entry(0, 0, True)
            elif stat.S_ISREG(info.st_mode):
                entries[key] = Entry(info.st_size, int(info.st_mtime))
    return entries


def same(local: Entry, remote: Entry) -> bool:
    if local.is_dir or remote.is_dir:
        return local.is_dir == remote.is_dir
    return local.size == remote.size and abs(local.mtime - remote.mtime) <= MODIFY_WINDOW


class RemoteFS(Protocol):
    """The operations of the mirror on a remote folder; paths use "/"."""

    # Names that differ only by their case are the same file on the server
    case_insensitive: bool

    def scan(self, root: str) -> dict[str, Entry] | None:
        """Entries below root, relative to it; None when root does not exist."""
        ...

    def makedirs(self, path: str) -> None: ...

    def put(self, local: Path, path: str, mtime: int, progress: Callable[[int], None]) -> None:
        """Copy a file, replacing the remote one only once it is complete."""
        ...

    def remove(self, path: str) -> None: ...

    def rmdir(self, path: str) -> None: ...

    def check_name(self, path: str) -> str:
        """Why the server cannot hold this path ("": it can)."""
        ...

    def close(self) -> None: ...


def mirror(
    local_root: Path,
    fs: RemoteFS,
    remote_root: str,
    protect: Sequence[str] = (),
    progress: Progress | None = None,
) -> SyncResult:
    """Make remote_root a copy of local_root; return what changed."""
    result = SyncResult()
    local = scan_local(local_root)
    remote = fs.scan(remote_root)
    if remote is None:
        fs.makedirs(remote_root)
        remote = {}
    remote = {p: e for p, e in remote.items() if p.split("/", 1)[0] not in protect}
    # Names the server cannot hold, and names equal but for the case on a
    # server that ignores it: reported, not copied
    rejected: set[str] = set()
    seen: dict[str, str] = {}
    for path in sorted(local):
        reason = fs.check_name(path)
        folded = path.casefold() if fs.case_insensitive else path
        if not reason and folded in seen:
            reason = f"same name as {seen[folded]} on the server (case ignored)"
        if any(path.startswith(f"{r}/") for r in rejected):
            reason = reason or "in a folder that cannot be copied"
        if reason:
            rejected.add(path)
            if not local[path].is_dir:
                result.errors.append((path, reason))
            continue
        seen[folded] = path
    wanted = {p: e for p, e in local.items() if p not in rejected}

    # Remote entries in the way, or not wanted any more: deepest first
    for path in sorted(remote, key=lambda p: p.count("/"), reverse=True):
        entry = wanted.get(path)
        if entry is not None and (entry.is_dir == remote[path].is_dir):
            continue
        target = f"{remote_root}/{path}"
        if remote[path].is_dir:
            fs.rmdir(target)
        else:
            fs.remove(target)
        result.deleted.append(path)
    kept = {p: e for p, e in remote.items() if p not in result.deleted}

    files = [p for p in sorted(wanted) if not wanted[p].is_dir]
    total = sum(wanted[p].size for p in files)
    done = 0
    for path in sorted(wanted):
        if wanted[path].is_dir and path not in kept:
            fs.makedirs(f"{remote_root}/{path}")
    for path in files:
        entry = wanted[path]
        if path in kept and same(entry, kept[path]):
            result.unchanged += 1
            done += entry.size
            continue
        sent = 0

        def advance(count: int, start: int = done) -> None:
            nonlocal sent
            sent = count
            if progress is not None:
                progress(start + sent, total)

        try:
            fs.put(local_root / path, f"{remote_root}/{path}", entry.mtime, advance)
        except OSError as ex:
            result.errors.append((path, ex.strerror or str(ex)))
        else:
            result.copied.append(path)
        done += entry.size
        if progress is not None:
            progress(done, total)
    return result


# -- SFTP ------------------------------------------------------------------------------


class SftpFS:
    """A folder on an SFTP server (paramiko); the host key must be known."""

    case_insensitive = False

    def __init__(self, server: Server, secrets: Secrets) -> None:
        import paramiko

        if server.known_hosts is None or not server.known_hosts.is_file():
            raise TransferError("no known_hosts file: the server cannot be authenticated")
        if secrets.ssh_key is None:
            raise TransferError("no SSH key (credential pandora-box.airgap-ssh-key)")
        self._client = paramiko.SSHClient()
        self._client.load_host_keys(str(server.known_hosts))
        self._client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            key = paramiko.PKey.from_path(secrets.ssh_key)
            self._client.connect(
                server.host,
                port=server.port or 22,
                username=server.user,
                pkey=key,
                look_for_keys=False,
                allow_agent=False,
                timeout=server.timeout,
                banner_timeout=server.timeout,
                auth_timeout=server.timeout,
            )
            self._sftp = self._client.open_sftp()
            self._sftp.get_channel().settimeout(server.timeout)
        except (OSError, paramiko.SSHException) as ex:
            self._client.close()
            raise TransferError(f"sftp: {ex}") from ex

    def scan(self, root: str) -> dict[str, Entry] | None:
        try:
            self._sftp.stat(root)
        except FileNotFoundError:
            return None
        entries: dict[str, Entry] = {}
        pending = [""]
        while pending:
            relative = pending.pop()
            for attr in self._sftp.listdir_attr(f"{root}/{relative}".rstrip("/")):
                path = f"{relative}/{attr.filename}".lstrip("/")
                mode = attr.st_mode or 0
                if stat.S_ISDIR(mode):
                    entries[path] = Entry(0, 0, True)
                    pending.append(path)
                else:
                    entries[path] = Entry(attr.st_size or 0, int(attr.st_mtime or 0))
        return entries

    def makedirs(self, path: str) -> None:
        current = ""
        for part in path.split("/"):
            current = f"{current}/{part}" if current or path.startswith("/") else part
            if not part:
                continue
            try:
                self._sftp.stat(current)
            except FileNotFoundError:
                self._sftp.mkdir(current)

    def put(self, local: Path, path: str, mtime: int, progress: Callable[[int], None]) -> None:
        tmp = _tmp_name(path)
        with local.open("rb") as source:
            self._sftp.putfo(source, tmp, callback=lambda sent, _total: progress(sent))
        self._sftp.utime(tmp, (mtime, mtime))
        self._sftp.posix_rename(tmp, path)

    def remove(self, path: str) -> None:
        self._sftp.remove(path)

    def rmdir(self, path: str) -> None:
        self._sftp.rmdir(path)

    def check_name(self, path: str) -> str:
        return ""

    def close(self) -> None:
        self._client.close()


# -- SMB -------------------------------------------------------------------------------


class SmbFS:
    """A folder of an SMB share (smbprotocol), SMB 3 with encryption."""

    case_insensitive = True

    def __init__(self, server: Server, secrets: Secrets) -> None:
        import smbclient
        from smbprotocol.exceptions import SMBException

        self._smb = smbclient
        self._server = server
        self._port = server.port or 445
        user = f"{server.domain}\\{server.user}" if server.domain else server.user
        try:
            smbclient.register_session(
                server.host,
                username=user,
                password=secrets.password,
                port=self._port,
                encrypt=True,
                connection_timeout=int(server.timeout),
            )
        except (OSError, SMBException, ValueError) as ex:
            raise TransferError(f"smb: {ex}") from ex
        self._prefix = f"\\\\{server.host}\\{server.share}"

    def _path(self, path: str) -> str:
        return self._prefix + "".join(f"\\{p}" for p in path.split("/") if p)

    def scan(self, root: str) -> dict[str, Entry] | None:
        try:
            self._smb.stat(self._path(root), port=self._port)
        except OSError as ex:
            # smbprotocol raises an OSError of its own, not FileNotFoundError
            if ex.errno == errno.ENOENT:
                return None
            raise
        entries: dict[str, Entry] = {}
        pending = [""]
        while pending:
            relative = pending.pop()
            for entry in self._smb.scandir(self._path(f"{root}/{relative}"), port=self._port):
                path = f"{relative}/{entry.name}".lstrip("/")
                info = entry.stat()
                if entry.is_dir():
                    entries[path] = Entry(0, 0, True)
                    pending.append(path)
                else:
                    entries[path] = Entry(info.st_size, int(info.st_mtime))
        return entries

    def makedirs(self, path: str) -> None:
        self._smb.makedirs(self._path(path), exist_ok=True, port=self._port)

    def put(self, local: Path, path: str, mtime: int, progress: Callable[[int], None]) -> None:
        tmp = self._path(_tmp_name(path))
        sent = 0
        with (
            local.open("rb") as source,
            self._smb.open_file(tmp, mode="wb", port=self._port) as target,
        ):
            while chunk := source.read(CHUNK):
                target.write(chunk)
                sent += len(chunk)
                progress(sent)
        self._smb.utime(tmp, times=(mtime, mtime), port=self._port)
        self._smb.replace(tmp, self._path(path), port=self._port)

    def remove(self, path: str) -> None:
        self._smb.remove(self._path(path), port=self._port)

    def rmdir(self, path: str) -> None:
        self._smb.rmdir(self._path(path), port=self._port)

    def check_name(self, path: str) -> str:
        for part in path.split("/"):
            if set(part) & _SMB_FORBIDDEN:
                return "name with characters an SMB server refuses"
            if part.endswith((" ", ".")):
                return "name ending with a space or a dot"
        return ""

    def close(self) -> None:
        # Closing: nothing left to do on an error
        with contextlib.suppress(Exception):
            self._smb.delete_session(self._server.host, port=self._port)


def _tmp_name(path: str) -> str:
    folder, _, name = path.rpartition("/")
    return f"{folder}/.{name}.pandora-box-tmp"


# -- rsync over SSH --------------------------------------------------------------------


def ssh_command(server: Server, secrets: Secrets) -> list[str]:
    """The ssh command of rsync: key only, known host only, no prompt."""
    if server.known_hosts is None or not server.known_hosts.is_file():
        raise TransferError("no known_hosts file: the server cannot be authenticated")
    if secrets.ssh_key is None:
        raise TransferError("no SSH key (credential pandora-box.airgap-ssh-key)")
    return [
        SSH,
        "-F", "none",
        "-i", str(secrets.ssh_key),
        "-p", str(server.port or 22),
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={server.known_hosts}",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", f"ConnectTimeout={int(server.timeout)}",
        "-o", f"ServerAliveInterval={max(5, int(server.timeout) // 4)}",
        "-o", "ServerAliveCountMax=4",
    ]  # fmt: skip


def rsync_mirror(
    local_root: Path,
    server: Server,
    secrets: Secrets,
    remote_root: str,
    protect: Sequence[str] = (),
) -> SyncResult:
    """Make remote_root a copy of local_root with rsync over SSH."""
    ssh = " ".join(_quote(a) for a in ssh_command(server, secrets))
    excludes = [f"--exclude=/{name}" for name in protect]
    argv = [
        RSYNC, "--recursive", "--times", "--delete", "--mkpath", "--no-links",
        f"--modify-window={MODIFY_WINDOW}", "--chmod=D0755,F0644",
        "--out-format=%i %n", f"--timeout={int(server.timeout)}", *excludes,
        "-e", ssh, f"{local_root}/", f"{_destination(server)}:{remote_root}/",
    ]  # fmt: skip
    output = _run_rsync(argv)
    result = SyncResult()
    for line in output.splitlines():
        # %i is 11 characters wide ("*deleting" padded), then a space
        flags, name = line[:11].strip(), line[12:]
        if flags == "*deleting":
            result.deleted.append(name.rstrip("/"))
        elif flags.startswith("<f"):
            result.copied.append(name)
    result.unchanged = sum(1 for e in scan_local(local_root).values() if not e.is_dir) - len(
        result.copied
    )
    return result


def rsync_put(local: Path, server: Server, secrets: Secrets, remote_root: str, name: str) -> None:
    """Copy the scan report into the .pandora-box folder of remote_root (rsync over SSH).

    Sent as a folder, without --delete: rrsync on the server refuses a
    single file sent again to the same path.
    """
    ssh = " ".join(_quote(a) for a in ssh_command(server, secrets))
    with tempfile.TemporaryDirectory(prefix="pandora-box-report-") as tmp:
        folder = Path(tmp, REPORTS)
        folder.mkdir()
        shutil.copy2(local, folder / name)
        _run_rsync(
            [RSYNC, "--recursive", "--times", "--chmod=D0755,F0644",
             f"--timeout={int(server.timeout)}", "-e", ssh,
             f"{tmp}/", f"{_destination(server)}:{remote_root}/"]
        )  # fmt: skip


def _run_rsync(argv: list[str]) -> str:
    try:
        result = subprocess.run(  # noqa: S603  (fixed command, no shell)
            argv, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL
        )
    except OSError as ex:
        raise TransferError(f"rsync: {ex}") from ex
    if result.returncode != 0:
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        raise TransferError(f"rsync failed ({result.returncode}): {lines[-1] if lines else ''}")
    return result.stdout


def _destination(server: Server) -> str:
    return f"{server.user}@{server.host}" if server.user else server.host


def _quote(value: str) -> str:
    """Quote a word of the -e command of rsync (split by rsync like a shell)."""
    if value and all(c.isalnum() or c in "-_./=:@,%+" for c in value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


# -- entry point -----------------------------------------------------------------------


def open_fs(server: Server, secrets: Secrets) -> RemoteFS:
    if server.protocol == "sftp":
        return SftpFS(server, secrets)
    if server.protocol == "smb":
        return SmbFS(server, secrets)
    raise ValueError(f"no file access for {server.protocol}")


def upload(
    local_root: Path,
    report: Path,
    server: Server,
    secrets: Secrets,
    remote_root: str,
    report_name: str,
    progress: Progress | None = None,
    fs_factory: Callable[[Server, Secrets], RemoteFS] = open_fs,
) -> SyncResult:
    """Mirror local_root to remote_root, then put the report in its .pandora-box folder."""
    if server.protocol == "rsync":
        result = rsync_mirror(local_root, server, secrets, remote_root, (REPORTS,))
        rsync_put(report, server, secrets, remote_root, report_name)
        return result
    fs = fs_factory(server, secrets)
    try:
        result = mirror(local_root, fs, remote_root, (REPORTS,), progress)
        fs.makedirs(f"{remote_root}/{REPORTS}")
        fs.put(report, f"{remote_root}/{REPORTS}/{report_name}", int(report.stat().st_mtime),
               lambda _sent: None)  # fmt: skip
        return result
    except OSError as ex:
        raise TransferError(f"{server.protocol}: {ex}") from ex
    finally:
        fs.close()


def describe(result: SyncResult) -> dict[str, Any]:
    return {
        "copied": len(result.copied),
        "deleted": len(result.deleted),
        "unchanged": result.unchanged,
        "errors": [[p, r] for p, r in result.errors],
    }
