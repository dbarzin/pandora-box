"""Mirror of a local folder to a remote server: rsync over SSH or SFTP (airgap).

The remote folder becomes a copy of the local one, in one direction: new and
changed files (size, modification time) are copied, files and folders that
are not in the local folder any more are deleted. Names listed in protect
(the reports of the kiosk) are left alone at the top of the remote folder.

rsync runs the OpenSSH client and needs rsync on the server. SFTP (paramiko)
uses the same comparison, written here. The server is always authenticated:
its SSH host key must be listed in known_hosts.
"""

from __future__ import annotations

import calendar
import contextlib
import os
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

RSYNC = "/usr/bin/rsync"
SSH = "/usr/bin/ssh"
# Modification times differ by up to 2 seconds between filesystems (vfat)
MODIFY_WINDOW = 2
CHUNK = 1024 * 1024
# Names a Windows server (NTFS) refuses: characters, and device names, with
# or without an extension
_WINDOWS_FORBIDDEN = set('\\:*?"<>|') | {chr(c) for c in range(32)}
_WINDOWS_DEVICES = {"con", "prn", "aux", "nul"} | {
    f"{d}{n}" for d in ("com", "lpt") for n in "123456789"
}
# Folder of the scan reports, at the top of the folder of a key on the server
REPORTS = ".pandora-box"
# Files waiting on the server to be copied to the key (outbound station)
OUTBOUND = f"{REPORTS}/outbound"

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
    # Folder on the server
    path: str
    known_hosts: Path | None = None
    timeout: float = 60.0
    # The server is Windows (OpenSSH): NTFS names, case ignored
    windows_names: bool = False


@dataclass(frozen=True)
class Secrets:
    """Credentials of the account (systemd credentials of the service)."""

    ssh_key: Path | None = None


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

    def scan(self, root: str) -> dict[str, Entry] | None:
        """Entries below root, relative to it; None when root does not exist."""
        ...

    def makedirs(self, path: str) -> None: ...

    def put(self, local: Path, path: str, mtime: int, progress: Callable[[int], None]) -> None:
        """Copy a file, replacing the remote one only once it is complete."""
        ...

    def get(self, path: str, local: Path, mtime: int) -> None:
        """Copy a remote file to local, with its modification time."""
        ...

    def remove(self, path: str) -> None: ...

    def rmdir(self, path: str) -> None: ...

    def close(self) -> None: ...


def windows_name(path: str) -> str:
    """Why a Windows server (NTFS) cannot hold this path ("": it can)."""
    for part in path.split("/"):
        if set(part) & _WINDOWS_FORBIDDEN:
            return 'name with characters Windows refuses (\\ : * ? " < > |)'
        if part.endswith((" ", ".")):
            return "name ending with a space or a dot, refused by Windows"
        if part.split(".", 1)[0].casefold() in _WINDOWS_DEVICES:
            return "name of a Windows device (CON, NUL, COM1...)"
    return ""


def mirror(
    local_root: Path,
    fs: RemoteFS,
    remote_root: str,
    protect: Sequence[str] = (),
    progress: Progress | None = None,
    windows_names: bool = False,
) -> SyncResult:
    """Make remote_root a copy of local_root; return what changed.

    windows_names: names Windows refuses, and names equal to another one but
    for the case, are not copied and reported in the errors.
    """
    result = SyncResult()
    local = scan_local(local_root)
    remote = fs.scan(remote_root)
    if remote is None:
        fs.makedirs(remote_root)
        remote = {}
    remote = {p: e for p, e in remote.items() if p.split("/", 1)[0] not in protect}
    wanted = local
    if windows_names:
        wanted = _windows_wanted(local, result)

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
        try:
            self._sftp.posix_rename(tmp, path)
        except OSError:
            # A server without the posix-rename extension: SFTP rename refuses
            # an existing target, which is deleted first (not atomic)
            with contextlib.suppress(FileNotFoundError):
                self._sftp.remove(path)
            self._sftp.rename(tmp, path)

    def get(self, path: str, local: Path, mtime: int) -> None:
        with local.open("xb") as target:
            self._sftp.getfo(path, target)
        os.utime(local, (mtime, mtime))

    def remove(self, path: str) -> None:
        self._sftp.remove(path)

    def rmdir(self, path: str) -> None:
        self._sftp.rmdir(path)

    def close(self) -> None:
        self._client.close()


def _windows_wanted(local: dict[str, Entry], result: SyncResult) -> dict[str, Entry]:
    """The entries a Windows server can hold; the others are reported in result."""
    rejected: set[str] = set()
    seen: dict[str, str] = {}
    for path in sorted(local):
        reason = windows_name(path)
        parent = path.rpartition("/")[0]
        if not reason and parent in rejected:
            reason = "in a folder that cannot be copied"
        folded = path.casefold()
        if not reason and folded in seen:
            reason = f"same name as {seen[folded]} for Windows (case ignored)"
        if reason:
            rejected.add(path)
            if not local[path].is_dir:
                result.errors.append((path, reason))
            continue
        seen[folded] = path
    return {p: e for p, e in local.items() if p not in rejected}


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


def _run_rsync(argv: list[str], missing_ok: bool = False) -> str:
    """Run rsync; missing_ok: a missing remote folder gives an empty output."""
    try:
        result = subprocess.run(  # noqa: S603  (fixed command, no shell)
            argv, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
            # The times listed by rsync are in the time zone of the client
            env={**os.environ, "TZ": "UTC"},
        )  # fmt: skip
    except OSError as ex:
        raise TransferError(f"rsync: {ex}") from ex
    if missing_ok and result.returncode == 23 and "No such file or directory" in result.stderr:
        return ""
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
        result = mirror(local_root, fs, remote_root, (REPORTS,), progress, server.windows_names)
        fs.makedirs(f"{remote_root}/{REPORTS}")
        fs.put(report, f"{remote_root}/{REPORTS}/{report_name}", int(report.stat().st_mtime),
               lambda _sent: None)  # fmt: skip
        return result
    except OSError as ex:
        raise TransferError(f"{server.protocol}: {ex}") from ex
    finally:
        fs.close()


# -- outbound: from the server to the key -----------------------------------------------


@dataclass
class FetchResult:
    # Files downloaded: new or changed compared with the key
    fetched: list[str] = field(default_factory=list)
    unchanged: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


def relative_path(path: str) -> str:
    """Why a path of the server cannot be one below a folder ("": it can)."""
    parts = path.split("/")
    if not path or path.startswith("/") or "\0" in path:
        return "invalid path"
    if any(part in ("", ".", "..") for part in parts):
        return "invalid path"
    return ""


def fetch(
    server: Server,
    secrets: Secrets,
    remote_root: str,
    local_dir: Path,
    have: dict[str, Entry],
    fs_factory: Callable[[Server, Secrets], RemoteFS] = open_fs,
) -> FetchResult:
    """Download the files of the outbound folder of remote_root that the key does not have.

    have: the files of the key (path, size, modification time); a file of the
    server is downloaded when it is not on the key, or differs (size, time).
    """
    remote = f"{remote_root}/{OUTBOUND}"
    if server.protocol == "rsync":
        result = _choose(rsync_list(server, secrets, remote), have)
        rsync_fetch(server, secrets, remote, local_dir, result.fetched)
        result.fetched = [p for p in result.fetched if (local_dir / p).is_file()]
        return result
    fs = fs_factory(server, secrets)
    try:
        entries = fs.scan(remote) or {}
        result = _choose(entries, have)
        for path in result.fetched:
            target = local_dir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            fs.get(f"{remote}/{path}", target, entries[path].mtime)
        return result
    except OSError as ex:
        raise TransferError(f"{server.protocol}: {ex}") from ex
    finally:
        fs.close()


def _choose(entries: dict[str, Entry], have: dict[str, Entry]) -> FetchResult:
    """The files of the server to download: new or changed (in fetched)."""
    result = FetchResult()
    for path, entry in sorted(entries.items()):
        if entry.is_dir:
            continue
        reason = relative_path(path)
        if reason:
            result.errors.append((path, reason))
        elif path in have and same(entry, have[path]):
            result.unchanged += 1
        else:
            result.fetched.append(path)
    return result


def send_report(
    report: Path,
    server: Server,
    secrets: Secrets,
    remote_root: str,
    report_name: str,
    fs_factory: Callable[[Server, Secrets], RemoteFS] = open_fs,
) -> None:
    """Put the report alone in the .pandora-box folder of remote_root."""
    if server.protocol == "rsync":
        rsync_put(report, server, secrets, remote_root, report_name)
        return
    fs = fs_factory(server, secrets)
    try:
        fs.makedirs(f"{remote_root}/{REPORTS}")
        fs.put(report, f"{remote_root}/{REPORTS}/{report_name}", int(report.stat().st_mtime),
               lambda _sent: None)  # fmt: skip
    except OSError as ex:
        raise TransferError(f"{server.protocol}: {ex}") from ex
    finally:
        fs.close()


def rsync_list(server: Server, secrets: Secrets, remote: str) -> dict[str, Entry]:
    """The files below a remote folder (rsync --list-only); empty when it is missing."""
    ssh = " ".join(_quote(a) for a in ssh_command(server, secrets))
    argv = [
        RSYNC, "--recursive", "--list-only", "--no-human-readable", "--8-bit-output",
        f"--timeout={int(server.timeout)}", "-e", ssh, f"{_destination(server)}:{remote}/",
    ]  # fmt: skip
    output = _run_rsync(argv, missing_ok=True)
    entries: dict[str, Entry] = {}
    for line in output.splitlines():
        fields = line.split(None, 4)
        if len(fields) != 5 or fields[4] == ".":
            continue
        mode, size, day, clock, name = fields
        stamp = calendar.timegm(time.strptime(f"{day} {clock}", "%Y/%m/%d %H:%M:%S"))
        if mode.startswith("d"):
            entries[name] = Entry(0, 0, True)
        elif mode.startswith("-"):
            entries[name] = Entry(int(size), stamp)
    return entries


def rsync_fetch(
    server: Server, secrets: Secrets, remote: str, local_dir: Path, paths: Sequence[str]
) -> None:
    """Download these files of a remote folder into local_dir (rsync over SSH)."""
    if not paths:
        return
    ssh = " ".join(_quote(a) for a in ssh_command(server, secrets))
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".list") as listing:
        listing.write("\0".join(paths) + "\0")
        listing.flush()
        _run_rsync(
            [RSYNC, "--times", "--no-links", "--from0", f"--files-from={listing.name}",
             f"--timeout={int(server.timeout)}", "-e", ssh,
             f"{_destination(server)}:{remote}/", f"{local_dir}/"]
        )  # fmt: skip


def describe(result: SyncResult) -> dict[str, Any]:
    return {
        "copied": len(result.copied),
        "deleted": len(result.deleted),
        "unchanged": result.unchanged,
        "errors": [[p, r] for p, r in result.errors],
    }
