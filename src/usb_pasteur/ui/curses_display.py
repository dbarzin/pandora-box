"""Full screen text interface (administrator mode)."""

from __future__ import annotations

import contextlib
import curses
import fcntl
import os
import struct
import termios
import textwrap
from collections import deque
from collections.abc import Sequence
from pathlib import Path

from usb_pasteur.device import UsbDevice
from usb_pasteur.logs import get_logger, log_event
from usb_pasteur.results import FileResult
from usb_pasteur.text import human_size, printable
from usb_pasteur.ui.base import Screen, video_mode
from usb_pasteur.ui.touch import Mice

logger = get_logger("display")

LOGO = (
    "░█░█░█▀▀░█▀▄░░░░░█▀█░█▀█░█▀▀░▀█▀░█▀▀░█░█░█▀▄",
    "░█░█░▀▀█░█▀▄░▄▄▄░█▀▀░█▀█░▀▀█░░█░░█▀▀░█░█░█▀▄",
    "░▀▀▀░▀▀▀░▀▀░░░░░░▀░░░▀░▀░▀▀▀░░▀░░▀▀▀░▀▀▀░▀░▀",
)

_TITLE_HEIGHT = len(LOGO) + 2
_STATUS_HEIGHT = 5
_PROGRESS_HEIGHT = 3

_RED, _BLUE, _GREEN = 1, 2, 3

# Font of the Linux console (fbcon) for these resolutions
_FONT_WIDTH, _FONT_HEIGHT = 8, 16


def screen_size(cmdline: str) -> tuple[int, int] | None:
    """Lines and columns of the screen set on the kernel command line."""
    mode = video_mode(cmdline)
    if mode is None:
        return None
    return mode[1] // _FONT_HEIGHT, mode[0] // _FONT_WIDTH


def fit_console(fd: int = 1, cmdline: Path = Path("/proc/cmdline")) -> tuple[int, int] | None:
    """Shrink the console to the screen of the kernel command line (video=).

    The Intel driver keeps the framebuffer of the firmware (1024x768 on the
    ThinkCentre) when it is large enough for the mode of the screen
    (1024x600): the console is then taller than the screen. On a Linux
    console, resizing the terminal resizes the console itself, drawn in the
    visible part. Return the new size, or None when nothing changed.
    """
    try:
        wanted = screen_size(cmdline.read_text())
        size = os.get_terminal_size(fd)
    except OSError:
        return None
    if wanted is None:
        return None
    lines, cols = min(size.lines, wanted[0]), min(size.columns, wanted[1])
    if (lines, cols) == (size.lines, size.columns):
        return None
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", lines, cols, 0, 0))
    except OSError:
        return None
    return lines, cols


class CursesDisplay:
    def __init__(self) -> None:
        self.screen: curses.window | None = None
        self.status_win: curses.window | None = None
        self.progress_win: curses.window | None = None
        self.log_win: curses.window | None = None
        self.logs: deque[str] = deque()
        self._percent = -1
        # What is shown, to draw it again when the screen size changes
        self.device: UsbDevice | None = None
        self.usage: tuple[int, int] | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        fitted = fit_console()
        self.screen = curses.initscr()
        curses.noecho()
        curses.cbreak()
        self.screen.keypad(True)
        curses.curs_set(0)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        if curses.has_colors():
            curses.start_color()
            curses.init_pair(_RED, curses.COLOR_RED, curses.COLOR_BLACK)
            curses.init_pair(_BLUE, curses.COLOR_BLUE, curses.COLOR_BLACK)
            curses.init_pair(_GREEN, curses.COLOR_GREEN, curses.COLOR_BLACK)
        # The interface is drawn in other windows; getch() refreshes the
        # standard screen, which would then blank them unless already drawn
        self.screen.refresh()
        self._layout()
        log_event(
            logger,
            "display_started",
            lines=curses.LINES,
            cols=curses.COLS,
            fitted=fitted is not None,
        )

    def stop(self) -> None:
        if self.screen is not None:
            curses.flushinp()
            curses.nocbreak()
            self.screen.keypad(False)
            curses.echo()
            curses.endwin()
            self.screen = None

    def _resized(self, key_resize: bool = False) -> bool:
        """Follow a change of the console size; return True when it changed.

        The graphics driver may change the resolution after the kiosk started
        (the firmware framebuffer, then the native mode of the screen): the
        interface is drawn again for the new size, else part of it would be
        off the screen. key_resize: getch() returned KEY_RESIZE, ncurses has
        already resized its screen (resizeterm() would queue another one).
        """
        if self.screen is None:
            return False
        # The kernel resized the console (graphics driver): fit it again
        fit_console()
        try:
            size = os.get_terminal_size(1)
        except OSError:
            return False
        if key_resize:
            curses.update_lines_cols()
        elif (size.lines, size.columns) == (curses.LINES, curses.COLS):
            return False
        else:
            curses.resizeterm(size.lines, size.columns)
        self.screen.clear()
        self.screen.refresh()
        self._layout()
        log_event(logger, "display_resized", lines=curses.LINES, cols=curses.COLS)
        return True

    def _layout(self) -> None:
        lines, cols = curses.LINES, curses.COLS
        title = curses.newwin(_TITLE_HEIGHT, cols, 0, 0)
        col = max(0, (cols - len(LOGO[0])) // 2)
        for i, line in enumerate(LOGO):
            _addstr(title, i + 1, col, line, curses.color_pair(_RED))
        title.refresh()

        top = _TITLE_HEIGHT + _STATUS_HEIGHT
        self.progress_win = curses.newwin(_PROGRESS_HEIGHT, cols, top, 0)
        percent, self._percent = max(0, self._percent), -1
        self._draw_progress(percent)

        self.status_win = curses.newwin(_STATUS_HEIGHT, cols, _TITLE_HEIGHT, 0)
        self._draw_device()

        top += _PROGRESS_HEIGHT
        self.log_win = curses.newwin(max(3, lines - top), cols, top, 0)
        # The latest lines are kept, as many as the window shows
        self.logs = deque(self.logs, maxlen=max(1, lines - top - 2))
        self._draw_logs()

    # -- Display protocol --------------------------------------------------

    def message(self, text: str) -> None:
        self._resized()
        # Long lines (file names) are wrapped, never cut at the screen edge
        width = max(10, curses.COLS - 2)
        for line in textwrap.wrap(printable(text), width, break_on_hyphens=False) or [""]:
            self.logs.append(line)
        self._draw_logs()

    # The messages already tell what the kiosk does
    def show(self, screen: Screen, files: Sequence[FileResult] = ()) -> None:
        pass

    def show_info(self, lines: Sequence[str]) -> None:
        pass

    def file_scanned(self, result: FileResult, done: int, total: int) -> None:
        pass

    def show_device(self, device: UsbDevice | None) -> None:
        self.device = device
        self.usage = None
        if not self._resized():
            self._draw_device()
        if device is None:
            self.progress(0)

    def show_usage(self, size: int, used: int) -> None:
        self.usage = (size, used)
        if not self._resized():
            self._draw_device()

    def progress(self, percent: int) -> None:
        if not self._resized():
            self._draw_progress(percent)

    def _draw_device(self) -> None:
        win = self.status_win
        if win is None:
            return
        d = self.device or UsbDevice(node="")
        win.erase()
        win.border(0)
        _addstr(win, 0, 1, " USB device ")
        half = curses.COLS // 2
        blue = curses.color_pair(_BLUE)
        _addstr(win, 1, 1, f"Label  : {printable(d.label)}", blue)
        _addstr(win, 2, 1, "Size   :", blue)
        _addstr(win, 3, 1, "Used   :", blue)
        _addstr(win, 1, half, f"Type   : {printable(d.fs_type)}", blue)
        _addstr(win, 2, half, f"Model  : {printable(d.vendor)} {printable(d.model)}", blue)
        _addstr(win, 3, half, f"Serial : {printable(d.serial)}", blue)
        if self.usage is not None:
            size, used = self.usage
            _addstr(win, 2, 1, f"Size   : {human_size(size)}", blue)
            _addstr(win, 3, 1, f"Used   : {human_size(used)}", blue)
        win.refresh()

    def _draw_progress(self, percent: int) -> None:
        win = self.progress_win
        if win is None or percent == self._percent:
            return
        self._percent = percent
        width = curses.COLS - 2
        win.erase()
        win.border(0)
        _addstr(win, 0, 1, f" Progress: {percent}% ")
        _addstr(win, 1, 1, "#" * (width * percent // 100))
        win.refresh()

    def confirm(self, prompt: str) -> None:
        """Wait for a key, a curses mouse event or a touch on the screen."""
        self.message(prompt)
        if self.screen is None:
            return
        curses.flushinp()
        self.screen.timeout(200)
        mice = Mice()
        try:
            # getch waits up to 200 ms, which paces the loop; a change of the
            # screen size is not a key press
            while True:
                key = self.screen.getch()
                if key == curses.KEY_RESIZE:
                    self._resized(key_resize=True)
                elif self._resized():
                    pass
                elif key != -1 or mice.released():
                    break
        finally:
            self.screen.timeout(-1)
            mice.close()

    # -- helpers -----------------------------------------------------------

    def _draw_logs(self) -> None:
        win = self.log_win
        if win is None:
            return
        win.erase()
        win.border(0)
        green = curses.color_pair(_GREEN)
        for i, line in enumerate(self.logs):
            _addstr(win, i + 1, 1, line[: curses.COLS - 2], green)
        win.refresh()


def _addstr(win: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    # Text that does not fit on a small screen is dropped
    with contextlib.suppress(curses.error):
        win.addstr(y, x, text, attr)
