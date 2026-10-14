"""Plain text display, for development without curses and for tests."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TextIO

from pandora_box.device import UsbDevice
from pandora_box.results import FileResult
from pandora_box.text import human_size, printable
from pandora_box.ui.base import Screen


class ConsoleDisplay:
    def __init__(self, out: TextIO | None = None, auto_confirm: bool | None = None) -> None:
        self.out = out or sys.stdout
        # Without a terminal there is nobody to press a key
        self.auto_confirm = not sys.stdin.isatty() if auto_confirm is None else auto_confirm
        self._last_percent = -1

    def start(self) -> None:
        self.message("PandoraBox started")

    def stop(self) -> None:
        self.message("PandoraBox stopped")

    def message(self, text: str) -> None:
        print(printable(text), file=self.out, flush=True)

    # The messages already tell what the kiosk does
    def show(self, screen: Screen, files: Sequence[FileResult] = ()) -> None:
        pass

    def show_info(self, lines: Sequence[str]) -> None:
        pass

    def file_scanned(self, result: FileResult, done: int, total: int) -> None:
        pass

    def show_device(self, device: UsbDevice | None) -> None:
        if device is not None:
            self.message(
                f"Device {device.node}: {device.vendor} {device.model} "
                f"serial={device.serial} label={device.label} fs={device.fs_type}"
            )
        self._last_percent = -1

    def show_usage(self, size: int, used: int) -> None:
        self.message(f"Size: {human_size(size)}, used: {human_size(used)}")

    def progress(self, percent: int) -> None:
        # Print every 10% only
        step = percent // 10 * 10
        if step != self._last_percent:
            self._last_percent = step
            self.message(f"Progress: {step}%")

    def confirm(self, prompt: str) -> None:
        self.message(prompt)
        if not self.auto_confirm:
            sys.stdin.readline()
