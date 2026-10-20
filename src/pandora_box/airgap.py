"""Copy of the clean files of a verified key to a server (airgap.enabled).

The kiosk has no network. Once a key is scanned and verified:

1. the kiosk reads the identifier of the key (pandora_box.keyid), or writes a
   new one on it: the only change made to the key, which is not cleaned;
2. it copies the clean files of the key into a staging folder
   (airgap.staging), each checked against the SHA-256 that was scanned: the
   malicious and suspicious files, and those not fully scanned, never are;
3. the service pandora-box-airgap (its own user, the only one besides the
   update and the SIEM services that the firewall lets out) mirrors that
   folder to <airgap.path>/<identifier>/ on the server, by rsync over SSH,
   or SFTP (pandora_box.remotes), with the scan report in its
   .pandora-box folder; then the kiosk deletes the staging folder.

The kiosk and the service talk over a Unix socket, one JSON line per
message: the request, then progress lines and the result.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import signal
import socket
import stat
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pandora_box.config import AirgapConfig, ConfigError, load_config
from pandora_box.keyid import UUID
from pandora_box.logs import LOGGER_NAME, JsonFormatter, get_logger, log_event, setup_logging
from pandora_box.remotes import Secrets, Server, SyncResult, TransferError, describe, upload

logger = get_logger("airgap")

DEFAULT_CONFIG = Path("/etc/pandora-box/pandora-box.toml")
# systemd credentials (LoadCredential=/ImportCredential= of the services)
SSH_KEY_CREDENTIAL = "pandora-box.airgap-ssh-key"
TOKEN_KEY_CREDENTIAL = "pandora-box.airgap-token-key"  # noqa: S105  (a file name)
# Files of the key in staging/<identifier>/files, the report next to them
FILES = "files"
REPORT = "report.json"
MAX_LINE = 64 * 1024
# The service tells it is alive this often during a long transfer
HEARTBEAT = 5.0


class AirgapError(Exception):
    """The files of the key could not be copied to the server."""


def credential(name: str) -> Path | None:
    """A systemd credential of this service; None when it is missing."""
    folder = os.environ.get("CREDENTIALS_DIRECTORY", "")
    path = Path(folder, name)
    return path if folder and path.is_file() else None


# -- kiosk: staging --------------------------------------------------------------------


@dataclass
class Staged:
    folder: Path
    files: list[str] = field(default_factory=list)
    # Files not copied to the staging folder, and why
    errors: list[tuple[str, str]] = field(default_factory=list)


def staging_folder(config: AirgapConfig, identifier: str) -> Path:
    if not UUID.match(identifier):
        raise AirgapError(f"invalid key identifier: {identifier!r}")
    return config.staging / identifier


def prepare(config: AirgapConfig, identifier: str, needed: int) -> Staged:
    """An empty staging folder for a key, with room for needed bytes."""
    folder = staging_folder(config, identifier)
    remove_staging(config, identifier)
    (folder / FILES).mkdir(mode=0o750, parents=True)
    free = shutil.disk_usage(folder).free
    if needed > free:
        remove_staging(config, identifier)
        raise AirgapError(
            f"not enough space on the kiosk to copy the key ({needed} bytes, {free} free)"
        )
    return Staged(folder)


def stage_file(staged: Staged, root: Path, relpath: str, target: str, sha256: str) -> None:
    """Copy a file of the mounted key into the staging folder, if it is the scanned one.

    relpath: path on the key; target: path in the staging folder.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    destination = staged.folder / FILES / target
    try:
        fd = os.open(root / relpath, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise OSError(0, "not a regular file")
            destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
            digest = hashlib.sha256()
            out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o640)
            try:
                while chunk := os.read(fd, 1024 * 1024):
                    digest.update(chunk)
                    os.write(out, chunk)
            finally:
                os.close(out)
            os.utime(destination, (info.st_mtime, info.st_mtime))
        finally:
            os.close(fd)
    except OSError as ex:
        destination.unlink(missing_ok=True)
        staged.errors.append((target, ex.strerror or str(ex)))
        return
    if digest.hexdigest() != sha256:
        destination.unlink()
        staged.errors.append((target, "changed since the scan"))
        return
    staged.files.append(target)


def remove_staging(config: AirgapConfig, identifier: str) -> None:
    shutil.rmtree(staging_folder(config, identifier), ignore_errors=True)


# -- kiosk: client ---------------------------------------------------------------------


@dataclass
class Transfer:
    """The result of a copy to the server."""

    copied: int = 0
    deleted: int = 0
    unchanged: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    destination: str = ""


def request(
    config: AirgapConfig,
    identifier: str,
    report_id: str,
    progress: Callable[[int, int], None] | None = None,
) -> Transfer:
    """Ask the service to copy the staging folder of a key; raise AirgapError."""
    staging_folder(config, identifier)
    # The service sends a line at least every HEARTBEAT seconds
    timeout = config.timeout + 4 * HEARTBEAT
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(config.socket))
            message = {"key": identifier, "report_id": report_id}
            sock.sendall(json.dumps(message).encode() + b"\n")
            for answer in _lines(sock):
                event = answer.get("event")
                if event == "progress" and progress is not None:
                    progress(_int(answer, "done"), _int(answer, "total"))
                elif event == "done":
                    return Transfer(
                        copied=_int(answer, "copied"),
                        deleted=_int(answer, "deleted"),
                        unchanged=_int(answer, "unchanged"),
                        errors=[
                            (str(e[0]), str(e[1]))
                            for e in answer.get("errors", [])
                            if isinstance(e, list) and len(e) == 2
                        ],
                        destination=str(answer.get("destination", "")),
                    )
                elif event == "failed":
                    raise AirgapError(str(answer.get("error", "copy failed")))
    except OSError as ex:
        raise AirgapError(f"copy service unavailable: {ex.strerror or ex}") from ex
    raise AirgapError("copy service: no result")


def _lines(sock: socket.socket) -> Iterator[dict[str, Any]]:
    buffer = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            return
        buffer += chunk
        if len(buffer) > MAX_LINE and b"\n" not in buffer:
            raise AirgapError("copy service: message too long")
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            try:
                value = json.loads(line)
            except ValueError:
                raise AirgapError("copy service: invalid message") from None
            if isinstance(value, dict):
                yield value


def _int(answer: dict[str, Any], key: str) -> int:
    value = answer.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# -- service ---------------------------------------------------------------------------


def server_of(config: AirgapConfig) -> Server:
    return Server(
        protocol=config.protocol,
        host=config.host,
        port=config.port,
        user=config.user,
        path=config.path,
        known_hosts=config.known_hosts,
        timeout=config.timeout,
        windows_names=config.windows_names,
    )


def secrets() -> Secrets:
    return Secrets(ssh_key=credential(SSH_KEY_CREDENTIAL))


def transfer(
    config: AirgapConfig,
    identifier: str,
    report_id: str,
    progress: Callable[[int, int], None] | None = None,
    upload_files: Callable[..., SyncResult] = upload,
) -> tuple[SyncResult, str]:
    """Mirror the staging folder of a key to the server; the remote folder too."""
    folder = staging_folder(config, identifier)
    files, report = folder / FILES, folder / REPORT
    if not files.is_dir() or not report.is_file():
        raise AirgapError(f"nothing staged for the key {identifier}")
    if not UUID.match(report_id):
        raise AirgapError("invalid report identifier")
    remote = f"{config.path.rstrip('/')}/{identifier}"
    server = server_of(config)
    result = upload_files(files, report, server, secrets(), remote, f"{report_id}.json", progress)
    return result, remote


def serve(
    config: AirgapConfig, transfer_key: Callable[..., tuple[SyncResult, str]] = transfer
) -> None:
    """Answer the requests of the kiosk, one at a time, until stopped."""
    path = config.socket
    # Ready once it appears: bound and listening under another name first
    pending = path.with_name(f".{path.name}.new")
    with contextlib.suppress(FileNotFoundError):
        pending.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(pending))
        server.listen(1)
        pending.chmod(0o660)
        pending.replace(path)
        log_event(logger, "airgap_ready", socket=str(path), protocol=config.protocol)
        while True:
            connection, _ = server.accept()
            with connection:
                handle(connection, config, transfer_key)


def handle(
    connection: socket.socket,
    config: AirgapConfig,
    transfer_key: Callable[..., tuple[SyncResult, str]] = transfer,
) -> None:
    lock = threading.Lock()

    def send(message: dict[str, Any]) -> None:
        with lock:
            connection.sendall(json.dumps(message).encode() + b"\n")

    connection.settimeout(config.timeout)
    try:
        request_line = next(_lines(connection))
    except (StopIteration, AirgapError, OSError):
        return
    identifier = str(request_line.get("key", ""))
    report_id = str(request_line.get("report_id", ""))
    log_event(logger, "airgap_started", key=identifier, report_id=report_id)
    done = threading.Event()

    def heartbeat() -> None:
        while not done.wait(HEARTBEAT):
            with contextlib.suppress(OSError):
                send({"event": "alive"})

    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        result, remote = transfer_key(
            config,
            identifier,
            report_id,
            lambda sent, total: send({"event": "progress", "done": sent, "total": total}),
        )
    except (AirgapError, TransferError, OSError) as ex:
        done.set()
        log_event(logger, "airgap_failed", logging.ERROR, key=identifier, error=str(ex))
        with contextlib.suppress(OSError):
            send({"event": "failed", "error": str(ex)})
        return
    done.set()
    summary = describe(result)
    log_event(logger, "airgap_done", key=identifier, destination=remote, **summary)
    with contextlib.suppress(OSError):
        send({"event": "done", "destination": remote, **summary})


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    path = Path(args[0]) if args else DEFAULT_CONFIG
    try:
        config = load_config(path)
    except ConfigError as ex:
        print(f"pandora-box-airgap: {ex}", file=sys.stderr)
        return 2
    # JSON lines on stderr: the journal of the service
    setup_logging(config.kiosk.name, config.logging.level, None)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(config.kiosk.name))
    logging.getLogger(LOGGER_NAME).addHandler(handler)
    if not config.airgap.enabled:
        print("pandora-box-airgap: the copy to a server is disabled ([airgap] enabled = false)")
        return 0
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    serve(config.airgap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
