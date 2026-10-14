"""Size of the console of the kiosk (curses interface)."""

from __future__ import annotations

import fcntl
import os
import struct
import termios
from collections.abc import Iterator
from pathlib import Path

import pytest

from pandora_box.ui.curses_display import LOGO, LOGO_SMALL, fit_console, screen_size, select_logo


@pytest.mark.parametrize(
    ("cmdline", "size"),
    [
        ("ro quiet video=1024x600M@60 panic=10", (37, 128)),
        ("video=HDMI-A-1:800x480@60", (30, 100)),
        ("video=efifb:off quiet", None),
        ("ro quiet", None),
    ],
)
def test_screen_size(cmdline: str, size: tuple[int, int] | None) -> None:
    assert screen_size(cmdline) == size


@pytest.mark.parametrize(
    ("lines", "cols", "logo"),
    [
        (48, 128, LOGO),
        (30, 100, LOGO),
        (25, 80, LOGO_SMALL),
        (24, 128, LOGO_SMALL),
    ],
)
def test_select_logo(lines: int, cols: int, logo: tuple[str, ...]) -> None:
    assert select_logo(lines, cols) is logo


@pytest.fixture
def terminal() -> Iterator[int]:
    """A pseudo-terminal of 48 lines, 128 columns: 1024x768 with an 8x16 font."""
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 48, 128, 0, 0))
    yield slave
    os.close(master)
    os.close(slave)


def test_console_shrunk_to_the_screen(tmp_path: Path, terminal: int) -> None:
    # The firmware framebuffer (1024x768) on a 1024x600 screen
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("ro video=1024x600M@60\n")
    assert fit_console(terminal, cmdline) == (37, 128)
    assert tuple(os.get_terminal_size(terminal)) == (128, 37)
    # Already fitted
    assert fit_console(terminal, cmdline) is None


def test_console_left_alone(tmp_path: Path, terminal: int) -> None:
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("ro quiet\n")
    assert fit_console(terminal, cmdline) is None
    # Never enlarged beyond the console
    cmdline.write_text("video=1920x1080\n")
    assert fit_console(terminal, cmdline) is None
    assert tuple(os.get_terminal_size(terminal)) == (128, 48)
