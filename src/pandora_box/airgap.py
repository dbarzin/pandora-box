"""Copy of the clean files of a verified key to a server (airgap.enabled).

The kiosk has no network. Once a key is scanned and verified:

1. the kiosk reads the token of the key (pandora_box.keyid), or writes a
   new one on it: the only change made to the key, which is not cleaned;
   the name of the folder of the key comes from a secret of the token;
2. it copies the clean files of the key into a staging folder
   (airgap.staging), each checked against the SHA-256 that was scanned: the
   malicious and suspicious files, and those not fully scanned, never are;
3. the service pandora-box-airgap (its own user, the only one besides the
   update and the SIEM services that the firewall lets out) mirrors that
   folder to <airgap.path>/<folder>/ on the server, by rsync over SSH,
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
from pandora_box.keyid import FOLDER, UUID
from pandora_box.logs import (
    LOGGER_NAME,
    JsonFormatter,
    get_logger,
    log_event,
    redact_key_fields,
    setup_logging,
)
from pandora_box.remotes import (
    Entry,
    FetchResult,
    Secrets,
    Server,
    SyncResult,
    TransferError,
    describe,
    fetch,
    send_report,
    upload,
)

logger = get_logger("airgap")

DEFAULT_CONFIG = Path("/etc/pandora-box/pandora-box.toml")
# systemd credentials (LoadCredential=/ImportCredential= of the services)
SSH_KEY_CREDENTIAL = "pandora-box.airgap-ssh-key"
TOKEN_KEY_CREDENTIAL = "pandora-box.airgap-token-key"  # noqa: S105  (a file name)
# Files of the key in staging/<folder>/files, the report next to them
FILES = "files"
REPORT = "report.json"
# Outbound: the files of the key (path, size, time), and those of the server
# to copy to it, downloaded by the service
KEY_FILES = "key-files.json"
OUTBOUND = "outbound"
# Operations of the service: copy the key to the server, download the files
# of the server for the key, send the report alone
OPERATIONS = ("inbound", "outbound", "report")
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


def staging_folder(config: AirgapConfig, folder: str) -> Path:
    """Where the files of a key are staged; folder: the name of its folder on the server."""
    if not FOLDER.match(folder):
        raise AirgapError("invalid key folder")
    return config.staging / folder


def hide(text: str, folder: str) -> str:
    """text without the name of the folder of a key (logs, screen)."""
    return text.replace(folder, "<key folder>") if folder else text


def prepare(config: AirgapConfig, key_folder: str, needed: int) -> Staged:
    """An empty staging folder for a key, with room for needed bytes."""
    folder = staging_folder(config, key_folder)
    remove_staging(config, key_folder)
    (folder / FILES).mkdir(mode=0o750, parents=True)
    free = shutil.disk_usage(folder).free
    if needed > free:
        remove_staging(config, key_folder)
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


def remove_staging(config: AirgapConfig, folder: str) -> None:
    shutil.rmtree(staging_folder(config, folder), ignore_errors=True)


# -- kiosk: client ---------------------------------------------------------------------


@dataclass
class Transfer:
    """The result of a copy to the server."""

    copied: int = 0
    deleted: int = 0
    unchanged: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    destination: str = ""
    # Outbound: files of the server downloaded for the key
    fetched: int = 0


def request(
    config: AirgapConfig,
    folder: str,
    report_id: str,
    progress: Callable[[int, int], None] | None = None,
    operation: str = "inbound",
) -> Transfer:
    """Ask the service to run an operation (OPERATIONS) for a key; raise AirgapError."""
    staging_folder(config, folder)
    # The service sends a line at least every HEARTBEAT seconds
    timeout = config.timeout + 4 * HEARTBEAT
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(config.socket))
            message = {"op": operation, "key": folder, "report_id": report_id}
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
                        fetched=_int(answer, "fetched"),
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
    key_folder: str,
    report_id: str,
    progress: Callable[[int, int], None] | None = None,
    upload_files: Callable[..., SyncResult] = upload,
) -> tuple[SyncResult, str]:
    """Mirror the staging folder of a key to the server; the remote folder too."""
    folder = staging_folder(config, key_folder)
    files, report = folder / FILES, folder / REPORT
    if not files.is_dir() or not report.is_file():
        raise AirgapError("nothing staged for the key")
    if not UUID.match(report_id):
        raise AirgapError("invalid report identifier")
    remote = f"{config.path.rstrip('/')}/{key_folder}"
    server = server_of(config)
    result = upload_files(files, report, server, secrets(), remote, f"{report_id}.json", progress)
    return result, remote


def prepare_outbound(config: AirgapConfig, key_folder: str, have: dict[str, Entry]) -> Path:
    """The folder where the service downloads the files of the server for a key.

    have: the files of the key, written for the service (KEY_FILES).
    """
    folder = staging_folder(config, key_folder)
    remove_staging(config, key_folder)
    outbound = folder / OUTBOUND
    outbound.mkdir(parents=True)
    folder.chmod(0o2750)
    # The service writes into it
    outbound.chmod(0o2770)
    files = {path: [entry.size, entry.mtime] for path, entry in have.items()}
    (folder / KEY_FILES).write_text(json.dumps(files), encoding="utf-8")
    return outbound


def receive(
    config: AirgapConfig, key_folder: str, fetch_files: Callable[..., FetchResult] = fetch
) -> FetchResult:
    """Download the files of the outbound folder of a key that it does not have."""
    folder = staging_folder(config, key_folder)
    outbound, listing = folder / OUTBOUND, folder / KEY_FILES
    if not outbound.is_dir() or not listing.is_file():
        raise AirgapError("nothing prepared for the key")
    try:
        data = json.loads(listing.read_text(encoding="utf-8"))
        have = {
            str(path): Entry(int(value[0]), int(value[1])) for path, value in dict(data).items()
        }
    except (ValueError, TypeError, IndexError) as ex:
        raise AirgapError("invalid list of the files of the key") from ex
    remote = f"{config.path.rstrip('/')}/{key_folder}"
    return fetch_files(server_of(config), secrets(), remote, outbound, have)


def report_only(
    config: AirgapConfig,
    key_folder: str,
    report_id: str,
    send: Callable[..., None] = send_report,
) -> None:
    """Send the report of a key alone (outbound station, or the final report)."""
    report = staging_folder(config, key_folder) / REPORT
    if not report.is_file():
        raise AirgapError("no report staged for the key")
    if not UUID.match(report_id):
        raise AirgapError("invalid report identifier")
    remote = f"{config.path.rstrip('/')}/{key_folder}"
    send(report, server_of(config), secrets(), remote, f"{report_id}.json")


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
    receive_key: Callable[..., FetchResult] = receive,
    report_key: Callable[..., None] = report_only,
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
    # The name of the folder of the key is never logged: the journal of the
    # service stays on the disk of the kiosk
    key_folder = str(request_line.get("key", ""))
    report_id = str(request_line.get("report_id", ""))
    operation = str(request_line.get("op", "inbound"))
    log_event(logger, "airgap_started", operation=operation, report_id=report_id)
    done = threading.Event()

    def heartbeat() -> None:
        while not done.wait(HEARTBEAT):
            with contextlib.suppress(OSError):
                send({"event": "alive"})

    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        if operation == "outbound":
            fetched = receive_key(config, key_folder)
        elif operation == "report":
            report_key(config, key_folder, report_id)
        elif operation == "inbound":
            result, remote = transfer_key(
                config,
                key_folder,
                report_id,
                lambda sent, total: send({"event": "progress", "done": sent, "total": total}),
            )
        else:
            raise AirgapError(f"unknown operation: {operation!r}")
    except (AirgapError, TransferError, OSError) as ex:
        done.set()
        error = hide(str(ex), key_folder)
        log_event(
            logger, "airgap_failed", logging.ERROR, operation=operation, report_id=report_id,
            error=error,
        )  # fmt: skip
        with contextlib.suppress(OSError):
            send({"event": "failed", "error": error})
        return
    done.set()
    reply: dict[str, Any] = {"event": "done"}
    if operation == "outbound":
        reply |= {
            "fetched": len(fetched.fetched),
            "unchanged": fetched.unchanged,
            "errors": [[p, r] for p, r in fetched.errors],
        }
    elif operation == "inbound":
        reply |= {"destination": remote, **describe(result)}
    # The names of the files go to the kiosk only
    logged = {
        k: (len(v) if k == "errors" else v)
        for k, v in reply.items()
        if k not in ("event", "destination")
    }
    log_event(logger, "airgap_done", operation=operation, report_id=report_id, **logged)
    with contextlib.suppress(OSError):
        send(reply)


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
    redact_key_fields(True)
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
