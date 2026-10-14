"""Minimal client for the clamd protocol over a Unix socket.

Only the commands needed by the kiosk are implemented, with the "z" prefix
(null-terminated commands and replies): PING, VERSION, FILDES (the open file
descriptor is passed to clamd, which may not be allowed to read the mount
point) and INSTREAM (the content is sent in length-prefixed chunks).

One connection is used per command: clamd closes it after the reply. Replies
are parsed strictly: anything unexpected is an error, never a clean answer.
"""

from __future__ import annotations

import os
import re
import socket
import struct
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

CHUNK_SIZE = 64 * 1024
# Replies are short: a bigger reply is an error
MAX_REPLY = 64 * 1024

_OK = re.compile(r"^(?:fd\[\d+\]|stream): OK$")
_FOUND = re.compile(r"^(?:fd\[\d+\]|stream): (\S+) FOUND$")
_VERSION = re.compile(r"^(ClamAV [^/]+)(?:/(\d+)/(.+))?$")


class ClamdError(Exception):
    """Communication failure or unexpected reply."""


class Status(StrEnum):
    OK = "OK"
    FOUND = "FOUND"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Reply:
    status: Status
    # Signature name (FOUND) or error message (ERROR)
    text: str = ""


@dataclass(frozen=True)
class ClamdVersion:
    engine: str  # "ClamAV 1.4.3"
    database: str = ""  # version of the daily database
    database_date: datetime | None = None


def parse_version(text: str) -> ClamdVersion:
    """Parse "ClamAV 1.4.3/27791/Thu Oct  2 09:12:00 2026"."""
    match = _VERSION.match(text.strip())
    if match is None:
        raise ClamdError(f"unexpected VERSION reply: {text[:100]!r}")
    engine, database, date_text = match.groups()
    date = None
    if date_text:
        try:
            # clamd prints the local time of the database build
            date = datetime.strptime(date_text, "%a %b %d %H:%M:%S %Y").astimezone(UTC)
        except ValueError:
            date = None
    return ClamdVersion(engine, database or "", date)


def parse_scan_replies(text: str) -> list[Reply]:
    """Parse the replies to a scan (several with clamd AllMatchScan)."""
    replies = []
    for line in text.split("\0"):
        line = line.strip("\n")
        if not line:
            continue
        if _OK.match(line):
            replies.append(Reply(Status.OK))
        elif match := _FOUND.match(line):
            replies.append(Reply(Status.FOUND, match.group(1)))
        elif line.endswith(" ERROR"):
            replies.append(Reply(Status.ERROR, line.removesuffix(" ERROR")[:200]))
        else:
            raise ClamdError(f"unexpected reply: {line[:100]!r}")
    if not replies:
        raise ClamdError("empty reply")
    return replies


class ClamdClient:
    def __init__(self, socket_path: Path, timeout: float) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def ping(self) -> None:
        reply = self._command(b"PING")
        if reply.strip("\0\n") != "PONG":
            raise ClamdError(f"unexpected PING reply: {reply[:100]!r}")

    def version(self) -> ClamdVersion:
        return parse_version(self._command(b"VERSION").strip("\0\n"))

    def scan_fd(self, fd: int) -> list[Reply]:
        """FILDES: clamd reads the file through the descriptor we pass."""
        deadline = time.monotonic() + self.timeout
        with self._connect() as sock:
            sock.sendall(b"zFILDES\0")
            # The descriptor travels in the ancillary data of a one-byte message
            socket.send_fds(sock, [b"\0"], [fd])
            return parse_scan_replies(self._read(sock, deadline))

    def scan_stream(self, fd: int) -> list[Reply]:
        """INSTREAM: send the content in chunks, then a zero-length chunk."""
        deadline = time.monotonic() + self.timeout
        with self._connect() as sock:
            try:
                sock.sendall(b"zINSTREAM\0")
                offset = 0
                while chunk := os.pread(fd, CHUNK_SIZE, offset):
                    self._settimeout(sock, deadline)
                    sock.sendall(struct.pack("!I", len(chunk)) + chunk)
                    offset += len(chunk)
                sock.sendall(struct.pack("!I", 0))
            except (BrokenPipeError, ConnectionResetError):
                # clamd stops reading at StreamMaxLength and sends an error
                pass
            return parse_scan_replies(self._read(sock, deadline))

    def _command(self, command: bytes) -> str:
        deadline = time.monotonic() + self.timeout
        with self._connect() as sock:
            sock.sendall(b"z" + command + b"\0")
            return self._read(sock, deadline)

    def _connect(self) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(self.socket_path))
        except OSError as ex:
            sock.close()
            raise ClamdError(f"cannot connect to {self.socket_path}: {ex.strerror or ex}") from ex
        return sock

    def _settimeout(self, sock: socket.socket, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ClamdError(f"timeout ({self.timeout:g}s)")
        sock.settimeout(remaining)

    def _read(self, sock: socket.socket, deadline: float) -> str:
        """Read the reply until clamd closes the connection."""
        data = bytearray()
        try:
            while True:
                self._settimeout(sock, deadline)
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if len(data) > MAX_REPLY:
                    raise ClamdError("reply too long")
        except TimeoutError as ex:
            raise ClamdError(f"timeout ({self.timeout:g}s)") from ex
        except ConnectionResetError as ex:
            if not data:
                raise ClamdError("connection reset by clamd") from ex
        return data.decode("utf-8", "replace")
