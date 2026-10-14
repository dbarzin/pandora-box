"""Graphical kiosk interface, drawn into the Linux framebuffer.

No display server, no browser: the screens are drawn with Pillow
(pandora_box.ui.screens) and copied into /dev/fb0. The console of the kiosk
(tty1) is put in graphics mode, so that the kernel does not draw its text
over them. A touch anywhere on the screen is the only input.
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import logging
import mmap
import os
import select
import struct
import termios
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

from pandora_box.config import DisplayConfig
from pandora_box.device import UsbDevice
from pandora_box.engines import Verdict
from pandora_box.logs import get_logger, log_event
from pandora_box.results import FileResult
from pandora_box.ui.base import RESULT_SCREENS, Screen, video_mode
from pandora_box.ui.screens import RECENT_FILES, Frame, Renderer, ViewState
from pandora_box.ui.theme import Theme
from pandora_box.ui.touch import Mice

logger = get_logger("display")

# linux/fb.h
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602
FBIOPAN_DISPLAY = 0x4606
# linux/kd.h
KDSETMODE = 0x4B3A
KDGETMODE = 0x4B3B
KD_TEXT = 0x00
KD_GRAPHICS = 0x01

# Screens that start a new step: the messages of the previous one are dropped
_NEW_STEP = frozenset({Screen.READY, Screen.SCANNING, Screen.CLEANING, Screen.SERVICE})
_MAX_MESSAGES = 50
# Minimum time between two drawings while the state changes (each scanned file)
_MIN_REDRAW = 0.15
# Longest wait of the drawing thread without anything to do
_IDLE_WAIT = 0.5
# The whole screen is copied again this often: the framebuffer may be cleared
# behind the kiosk (mode set by the graphics driver at boot, screen plugged
# again), and only the changed parts are copied otherwise
_FULL_REDRAW = 2.0


class FramebufferError(Exception):
    """Raised when the framebuffer cannot be used."""


@dataclass(frozen=True)
class Geometry:
    """The visible part of the framebuffer and its pixel format."""

    width: int
    height: int
    line_length: int
    bytes_per_pixel: int
    # Pillow raw mode of a pixel, e.g. "BGRX"
    raw_mode: str
    xoffset: int = 0
    yoffset: int = 0
    # Size of the framebuffer memory, in bytes (0: just the visible part)
    memory: int = 0
    # Name of the framebuffer driver, e.g. "i915drmfb"
    driver: str = ""

    @property
    def mapped(self) -> int:
        """Bytes of the framebuffer memory mapped to draw."""
        return self.memory or (self.yoffset + self.height) * self.line_length


def raw_mode(bits_per_pixel: int, red_offset: int, blue_offset: int) -> str:
    """Pillow raw mode of the pixels of the framebuffer (24 and 32 bits only)."""
    order = "BGR" if blue_offset < red_offset else "RGB"
    if bits_per_pixel == 32:
        return order + "X"
    if bits_per_pixel == 24:
        return order
    raise FramebufferError(f"unsupported framebuffer format: {bits_per_pixel} bits per pixel")


def read_geometry(fd: int, cmdline: Path = Path("/proc/cmdline")) -> Geometry:
    """Geometry of an open framebuffer.

    The graphics driver may keep the framebuffer of the firmware (1024x768 on
    the ThinkCentre) on a smaller screen (1024x600): only the mode set on the
    kernel command line (video=) is visible, the rest is never drawn.
    """
    try:
        var = fcntl.ioctl(fd, FBIOGET_VSCREENINFO, bytes(160))
        fix = fcntl.ioctl(fd, FBIOGET_FSCREENINFO, bytes(80))
    except OSError as ex:
        raise FramebufferError(f"not a framebuffer: {ex.strerror}") from ex
    xres, yres, _, _, xoffset, yoffset, bits, _, red_offset = struct.unpack_from("9I", var)
    blue_offset = struct.unpack_from("I", var, 4 * 14)[0]
    name, _, memory, *_, line_length = struct.unpack_from("@16sLIIIIHHHI", fix)
    width, height = xres, yres
    try:
        mode = video_mode(cmdline.read_text())
    except OSError:
        mode = None
    if mode is not None:
        width, height = min(width, mode[0]), min(height, mode[1])
    return Geometry(
        width=width,
        height=height,
        line_length=line_length,
        bytes_per_pixel=bits // 8,
        raw_mode=raw_mode(bits, red_offset, blue_offset),
        xoffset=xoffset,
        yoffset=yoffset,
        memory=memory,
        driver=name.split(b"\0", 1)[0].decode("ascii", "replace"),
    )


class Framebuffer:
    """Drawing through a memory mapping of the framebuffer.

    Not write(): the fbdev emulation of the DRM drivers (i915, bochs) copies
    the written range to the screen, and a range of exactly one full line is
    copied as an empty area. With a mapping, the drivers with a shadow buffer
    (bochs) track the pages that were modified (deferred I/O); i915 maps the
    memory of the screen itself, but must be told that it changed (flush()).
    """

    def __init__(self, path: Path, geometry: Geometry | None = None) -> None:
        """geometry: fixed geometry (tests), else read from the device."""
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        except OSError as ex:
            raise FramebufferError(f"cannot open {path}: {ex.strerror}") from ex
        self._fixed = geometry
        self._map: mmap.mmap | None = None
        try:
            self.geometry = self.read_geometry()
            self._mapping()
        except FramebufferError:
            os.close(self.fd)
            raise

    def _mapping(self) -> mmap.mmap:
        """The mapping of the framebuffer memory, made again when its size changes."""
        size = self.geometry.mapped
        if self._map is None or len(self._map) != size:
            if self._map is not None:
                self._map.close()
                self._map = None
            try:
                self._map = mmap.mmap(self.fd, size, mmap.MAP_SHARED, mmap.PROT_WRITE)
            except (OSError, ValueError) as ex:
                raise FramebufferError(f"cannot map the framebuffer: {ex}") from ex
        return self._map

    def read_geometry(self) -> Geometry:
        return self._fixed or read_geometry(self.fd)

    @property
    def size(self) -> tuple[int, int]:
        return self.geometry.width, self.geometry.height

    def write(self, image: Image.Image, box: tuple[int, int, int, int] | None = None) -> None:
        """Copy the image (of the framebuffer size), or the part of it in box."""
        g = self.geometry
        left, top, right, bottom = box or (0, 0, g.width, g.height)
        data = image.crop((left, top, right, bottom)).tobytes("raw", g.raw_mode)
        row = (right - left) * g.bytes_per_pixel
        memory = self._mapping()
        for y in range(top, bottom):
            offset = (y + g.yoffset) * g.line_length + (left + g.xoffset) * g.bytes_per_pixel
            start = (y - top) * row
            memory[offset : offset + row] = data[start : start + row]
        self.flush()

    def flush(self) -> None:
        """Tell the driver that the screen was drawn.

        Panning to the current position changes nothing on the screen, but
        i915 then stops showing its compressed copy of the framebuffer (FBC),
        which ignores the writes to the mapping. Drivers without panning
        (efifb) refuse it: their mapping is the screen.
        """
        if self._fixed is not None:
            return
        with contextlib.suppress(OSError):
            var = fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, bytes(160))
            fcntl.ioctl(self.fd, FBIOPAN_DISPLAY, var)

    def close(self) -> None:
        if self._map is not None:
            self._map.close()
            self._map = None
        os.close(self.fd)


class _Console:
    """The console of the kiosk: graphics mode, and keys read one by one."""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.mode: int | None = None
        self.attrs: list[Any] | None = None

    def start(self) -> None:
        if not os.isatty(self.fd):
            return
        with contextlib.suppress(OSError):
            buf = fcntl.ioctl(self.fd, KDGETMODE, struct.pack("i", 0))
            self.mode = struct.unpack("i", buf)[0]
            fcntl.ioctl(self.fd, KDSETMODE, KD_GRAPHICS)
        with contextlib.suppress(termios.error):
            self.attrs = termios.tcgetattr(self.fd)
            attrs = termios.tcgetattr(self.fd)
            attrs[3] &= ~(termios.ICANON | termios.ECHO)
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)

    def stop(self) -> None:
        if self.mode is not None:
            with contextlib.suppress(OSError):
                fcntl.ioctl(self.fd, KDSETMODE, KD_TEXT)
            self.mode = None
        if self.attrs is not None:
            with contextlib.suppress(termios.error):
                termios.tcsetattr(self.fd, termios.TCSANOW, self.attrs)
            self.attrs = None

    def flush(self) -> None:
        if os.isatty(self.fd):
            with contextlib.suppress(termios.error):
                termios.tcflush(self.fd, termios.TCIFLUSH)

    def key_pressed(self, timeout: float) -> bool:
        if not os.isatty(self.fd):
            time.sleep(timeout)
            return False
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if ready:
            os.read(self.fd, 64)
            return True
        return False


class FramebufferDisplay:
    def __init__(
        self,
        config: DisplayConfig,
        theme: Theme,
        framebuffer: Framebuffer | None = None,
        console_fd: int = 0,
    ) -> None:
        self.config = config
        self.theme = theme
        self.framebuffer = framebuffer
        # The console of the kiosk is its standard input (systemd TTYPath)
        self.console = _Console(console_fd)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = ViewState()
        self._version = 0
        # The waiting screen, shown at that time (time.monotonic()), once
        # the device of a result was removed
        self._pending: Screen | None = None
        self._pending_at = 0.0

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self.framebuffer is None:
            self.framebuffer = Framebuffer(self.config.framebuffer)
        self.console.start()
        log_event(
            logger,
            "display_started",
            width=self.framebuffer.geometry.width,
            height=self.framebuffer.geometry.height,
            format=self.framebuffer.geometry.raw_mode,
            driver=self.framebuffer.geometry.driver,
            language=self.theme.language,
        )
        self._thread = threading.Thread(target=self._run, name="display", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self.console.stop()
        if self.framebuffer is not None:
            self.framebuffer.close()
            self.framebuffer = None

    # -- Display protocol ----------------------------------------------------

    def message(self, text: str) -> None:
        self._update(messages=(*self._state.messages, text)[-_MAX_MESSAGES:])

    def show(self, screen: Screen, files: Sequence[FileResult] = ()) -> None:
        with self._lock:
            current = self._state.screen
            if screen in (Screen.READY, Screen.UNAVAILABLE) and current in RESULT_SCREENS:
                # The device was removed: its result stays on the screen a
                # little longer, then the kiosk waits for the next one
                self._pending = screen
                self._pending_at = time.monotonic() + self.config.return_seconds
                self._wake.set()
                return
        self._show(screen, files)

    def show_info(self, lines: Sequence[str]) -> None:
        self._update(info=tuple(lines))

    def show_device(self, device: UsbDevice | None) -> None:
        self._update(
            device=device, usage=None, percent=0, done=0, total=0, threats=0, current="", recent=()
        )

    def show_usage(self, size: int, used: int) -> None:
        self._update(usage=(size, used))

    def progress(self, percent: int) -> None:
        self._update(percent=percent)

    def file_scanned(self, result: FileResult, done: int, total: int) -> None:
        state = self._state
        threat = result.verdict in (Verdict.MALICIOUS, Verdict.SUSPICIOUS)
        self._update(
            done=done,
            total=total,
            threats=state.threats + threat,
            current=result.display_path,
            recent=(result, *state.recent)[:RECENT_FILES],
        )

    def confirm(self, prompt: str) -> None:
        """Wait for a touch anywhere on the screen (or a key press)."""
        self.console.flush()
        mice = Mice()
        try:
            while not self._stopping.is_set():
                if self.console.key_pressed(0.1) or mice.released():
                    break
        finally:
            mice.close()
        log_event(logger, "display_confirmed")

    # -- state ---------------------------------------------------------------

    def _show(self, screen: Screen, files: Sequence[FileResult]) -> None:
        with self._lock:
            self._pending = None
            messages = () if screen in _NEW_STEP else self._state.messages
            self._set(screen=screen, files=tuple(files), messages=messages)

    def _update(self, **changes: object) -> None:
        with self._lock:
            self._set(**changes)

    def _set(self, **changes: object) -> None:
        """Change the state (the lock is held)."""
        self._state = dataclasses.replace(self._state, **changes)  # type: ignore[arg-type]
        self._version += 1
        self._wake.set()

    # -- drawing thread ------------------------------------------------------

    def _run(self) -> None:
        fb = self.framebuffer
        if fb is None:
            return
        renderer: Renderer | None = None
        frame: Frame | None = None
        drawn = -1
        written: Image.Image | None = None
        logged: tuple[Screen, list[str]] | None = None
        start = time.monotonic()
        full_at = 0.0
        while not self._stopping.is_set():
            now = time.monotonic()
            try:
                with self._lock:
                    pending, pending_at = self._pending, self._pending_at
                if pending is not None and now >= pending_at:
                    self._show(pending, ())
                geometry = fb.read_geometry()
                if renderer is None or geometry != fb.geometry:
                    fb.geometry = geometry
                    renderer = Renderer(self.theme, fb.size)
                    drawn, written = -1, None
                with self._lock:
                    state, version = self._state, self._version
                if version != drawn:
                    frame = renderer.render(state)
                    drawn = version
                    logged = self._log_screen(state, frame, logged)
                if frame is None:
                    continue
                image = frame.image(now - start)
                if written is None or now - full_at >= _FULL_REDRAW:
                    box: tuple[int, int, int, int] | None = (0, 0, *fb.size)
                    full_at = now
                else:
                    box = _changed(written, image)
                if box is not None:
                    fb.write(image, box)
                written = image
            except (OSError, FramebufferError) as ex:
                log_event(logger, "display_failed", logging.ERROR, error=str(ex))
                self._stopping.wait(5)
                continue
            # Animated: the next image; then at most one drawing per _MIN_REDRAW
            wait = _IDLE_WAIT if frame.animation is None else frame.animation.interval
            self._stopping.wait(_MIN_REDRAW)
            self._wake.wait(max(0.0, wait - _MIN_REDRAW))
            self._wake.clear()

    @staticmethod
    def _log_screen(
        state: ViewState, frame: Frame, logged: tuple[Screen, list[str]] | None
    ) -> tuple[Screen, list[str]]:
        """Log the texts of the screen when they change (not every scanned file)."""
        current = (state.screen, frame.lines)
        if state.screen is Screen.SCANNING and logged is not None and logged[0] is Screen.SCANNING:
            return logged
        if current != logged:
            log_event(
                logger,
                "display_screen",
                logging.DEBUG,
                screen=state.screen.value,
                lines=frame.lines,
            )
        return current


def _changed(before: Image.Image, after: Image.Image) -> tuple[int, int, int, int] | None:
    """The box of the pixels that changed, None when none did."""
    if before is after:
        return None
    return ImageChops.difference(before, after).getbbox()
