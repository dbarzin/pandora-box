"""Touches on the screen of the kiosk."""

from __future__ import annotations

import os

MICE = "/dev/input/mice"


class Mice:
    """Touchscreens are seen as a mouse: read raw events when allowed.

    Opened when an answer is expected: touches made before are not read.
    """

    def __init__(self, path: str = MICE) -> None:
        self.fd: int | None
        try:
            self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError:
            self.fd = None
        self.pressed = False

    def released(self) -> bool:
        """Return True once a left button press has been followed by a release."""
        if self.fd is None:
            return False
        while True:
            try:
                buf = os.read(self.fd, 3)
            except BlockingIOError:
                return False
            if len(buf) < 3:
                return False
            if buf[0] & 0x1:
                self.pressed = True
            elif self.pressed:
                return True

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
