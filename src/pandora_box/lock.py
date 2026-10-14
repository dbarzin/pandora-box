"""Single-instance lock."""

from __future__ import annotations

import socket


class AlreadyRunningError(Exception):
    pass


class InstanceLock:
    """Hold an abstract Unix socket: released automatically when the process exits."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._socket: socket.socket | None = None

    def acquire(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            # A leading null byte creates the socket in the Linux abstract namespace
            sock.bind("\0" + self.name)
        except OSError as ex:
            sock.close()
            raise AlreadyRunningError(f"{self.name} is already running") from ex
        self._socket = sock

    def release(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
