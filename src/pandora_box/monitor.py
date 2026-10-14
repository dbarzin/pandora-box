"""Detection of USB storage devices with udev."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pandora_box.device import UsbDevice


class Action(StrEnum):
    ADD = "add"
    REMOVE = "remove"
    # No device event for a while: the kiosk may do its background work
    IDLE = "idle"


# Device of the IDLE events
NO_DEVICE = UsbDevice(node="")
# Seconds without device event before an IDLE event
IDLE_INTERVAL = 5.0


@dataclass(frozen=True)
class DeviceEvent:
    action: Action
    device: UsbDevice


class DeviceSource(Protocol):
    def wait_event(self) -> DeviceEvent | None:
        """Block until a USB filesystem is added or removed (None: stop).

        Sources may also return IDLE events when nothing happens.
        """
        ...


class UsbFilesystemFilter:
    """Select the udev block events of filesystems on USB devices.

    A removal is matched by the node of a reported insertion, not by its
    properties: once a device is ejected, its medium is empty and udev drops
    its filesystem properties before the device is removed.
    """

    def __init__(self) -> None:
        self._added: set[str] = set()

    def event(
        self, action: str, node: str | None, properties: Mapping[str, Any]
    ) -> DeviceEvent | None:
        if not node:
            return None
        if action == Action.ADD:
            if properties.get("ID_FS_USAGE") != "filesystem" or properties.get("ID_BUS") != "usb":
                return None
            self._added.add(node)
            return DeviceEvent(Action.ADD, UsbDevice.from_udev(properties, node))
        if action == Action.REMOVE and node in self._added:
            self._added.discard(node)
            return DeviceEvent(Action.REMOVE, UsbDevice.from_udev(properties, node))
        return None


class UdevSource:
    """Listen to udev block events for filesystems on USB devices."""

    def __init__(self) -> None:
        import pyudev

        self._monitor = pyudev.Monitor.from_netlink(pyudev.Context())
        self._monitor.filter_by("block")
        self._filter = UsbFilesystemFilter()

    def wait_event(self) -> DeviceEvent | None:
        while True:
            dev = self._monitor.poll(timeout=IDLE_INTERVAL)
            if dev is None:
                return DeviceEvent(Action.IDLE, NO_DEVICE)
            event = self._filter.event(dev.action, dev.device_node, dev)
            if event is not None:
                return event
