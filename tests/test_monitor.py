from __future__ import annotations

from pandora_box.monitor import Action, UsbFilesystemFilter

USB_VFAT = {"ID_FS_USAGE": "filesystem", "ID_BUS": "usb", "ID_FS_TYPE": "vfat"}


def test_filesystems_on_usb_devices_only() -> None:
    events = UsbFilesystemFilter()
    assert events.event("add", "/dev/sda", {**USB_VFAT, "ID_BUS": "ata"}) is None
    assert events.event("add", "/dev/sda", {"ID_BUS": "usb"}) is None
    assert events.event("add", None, USB_VFAT) is None
    assert events.event("change", "/dev/sda", USB_VFAT) is None
    added = events.event("add", "/dev/sda", USB_VFAT)
    assert added is not None
    assert added.action is Action.ADD
    assert added.device.fs_type == "vfat"


def test_removal_of_an_ejected_device() -> None:
    """After an eject, udev drops the filesystem properties of the empty medium."""
    events = UsbFilesystemFilter()
    events.event("add", "/dev/sda", USB_VFAT)
    removed = events.event("remove", "/dev/sda", {"ID_BUS": "usb"})
    assert removed is not None
    assert removed.action is Action.REMOVE
    assert removed.device.node == "/dev/sda"
    # Reported once
    assert events.event("remove", "/dev/sda", {"ID_BUS": "usb"}) is None


def test_removal_of_an_unknown_device_is_ignored() -> None:
    events = UsbFilesystemFilter()
    assert events.event("remove", "/dev/sdb", USB_VFAT) is None
