"""A key with several partitions: one scan, one verdict, one report."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from pathlib import Path

import jsonschema
import pytest

from pandora_box.config import Config
from pandora_box.device import DeviceError, UsbDevice
from pandora_box.engines.fake import EICAR
from pandora_box.kiosk import Kiosk, build_pool
from pandora_box.monitor import Action, DeviceEvent
from pandora_box.report import load_schema
from pandora_box.ui.base import Screen
from pandora_box.workers import WorkerPool

from .conftest import DirectoryMounter, ListSource, RecordingDisplay

FIRST = UsbDevice("/dev/sdb1", "vfat", "EFI")
SECOND = UsbDevice("/dev/sdb2", "exfat", "DATA")
KEY = UsbDevice(
    "/dev/sdb", "vfat,exfat", "EFI / DATA", "Key", "42", "Acme", partitions=(FIRST, SECOND)
)


class PartitionMounter(DirectoryMounter):
    """Each partition 'mounted' from its own folder; some cannot be mounted."""

    def __init__(self, roots: dict[str, Path]) -> None:
        super().__init__(next(iter(roots.values())))
        self.roots = roots
        self.refused: set[str] = set()
        self.refused_rw: set[str] = set()
        self.mounts: list[tuple[str, bool]] = []

    def mount(self, device: UsbDevice, read_only: bool = True) -> None:
        assert not self.mounted, "mounted twice"
        if device.node in self.refused or (not read_only and device.node in self.refused_rw):
            raise DeviceError(f"cannot mount {device.node}")
        self.mount_point = self.roots[device.node]
        self.mounts.append((device.node, read_only))
        super().mount(device, read_only)


@pytest.fixture
def pool(config: Config) -> Iterator[WorkerPool]:
    with build_pool(config) as pool:
        yield pool


@pytest.fixture
def roots(tmp_path: Path) -> dict[str, Path]:
    first = tmp_path / "sdb1"
    (first / "EFI").mkdir(parents=True)
    (first / "EFI" / "boot.efi").write_bytes(b"boot")
    second = tmp_path / "sdb2"
    (second / "docs").mkdir(parents=True)
    (second / "docs" / "eicar.com").write_bytes(EICAR)
    (second / "readme.txt").write_text("hello")
    return {"/dev/sdb1": first, "/dev/sdb2": second}


def run(
    config: Config, display: RecordingDisplay, mounter: DirectoryMounter, pool: WorkerPool,
    key: UsbDevice = KEY,
) -> dict[str, object]:  # fmt: skip
    Kiosk(config, display, ListSource([DeviceEvent(Action.ADD, key)]), pool, mounter).run()
    [report] = (Path(config.report.folder)).glob("*.json")
    data: dict[str, object] = json.loads(report.read_text())
    jsonschema.validate(data, load_schema())
    return data


def test_every_partition_is_scanned_and_cleaned(
    config: Config, display: RecordingDisplay, roots: dict[str, Path], pool: WorkerPool
) -> None:
    mounter = PartitionMounter(roots)
    report = run(config, display, mounter, pool)

    assert not (roots["/dev/sdb2"] / "docs" / "eicar.com").exists()
    assert (roots["/dev/sdb2"] / "readme.txt").exists()
    # Scanned one after the other, read-only; only the infected one read-write
    assert mounter.mounts == [("/dev/sdb1", True), ("/dev/sdb2", True), ("/dev/sdb2", False)]
    assert not mounter.mounted
    # One verdict, one confirmation, one ejection of the key
    assert [screen for screen, _ in display.screens] == [
        Screen.READY,
        Screen.SCANNING,
        Screen.THREATS,
        Screen.CLEANING,
        Screen.CLEANED,
    ]
    assert display.confirmations == 1
    assert display.screens[2][1] == ["sdb2/docs/eicar.com"]
    assert "sdb2/docs/eicar.com removed" in display.messages
    assert mounter.ejected == ["/dev/sdb"]
    assert display.percents[-1] == 100

    device = report["device"]
    assert isinstance(device, dict)
    assert device["node"] == "/dev/sdb"
    assert [(p["name"], p["filesystem"], p["scanned"]) for p in device["partitions"]] == [
        ("sdb1", "vfat", True),
        ("sdb2", "exfat", True),
    ]
    files = report["files"]
    assert isinstance(files, list)
    assert [(f["partition"], f["path"]) for f in files] == [
        ("sdb1", "EFI/boot.efi"),
        ("sdb2", "docs/eicar.com"),
        ("sdb2", "readme.txt"),
    ]
    assert report["actions"] == {
        "quarantine_folder": report["actions"]["quarantine_folder"],  # type: ignore[index]
        "quarantined": ["sdb2/docs/eicar.com"],
        "removed": ["sdb2/docs/eicar.com"],
        "remove_failed": [],
        "cleaned": True,
        "airgap": None,
    }
    [manifest] = Path(config.quarantine.folder).glob("*/manifest.json")
    [entry] = json.loads(manifest.read_text())["files"]
    assert (entry["partition"], entry["original_path"]) == ("sdb2", "docs/eicar.com")
    assert (manifest.parent / entry["file"]).read_bytes() == EICAR


def test_unscannable_partition_is_not_verified(
    config: Config, display: RecordingDisplay, roots: dict[str, Path], pool: WorkerPool
) -> None:
    (roots["/dev/sdb2"] / "docs" / "eicar.com").unlink()
    key = dataclasses.replace(KEY, unscannable=(("/dev/sdb3", "encrypted (crypto_LUKS)"),))
    report = run(config, display, PartitionMounter(roots), pool, key)

    assert display.screens[-1][0] is Screen.NOT_VERIFIED
    assert "Partition sdb3 NOT scanned: encrypted (crypto_LUKS)" in display.messages
    verdict = report["verdict"]
    assert isinstance(verdict, dict)
    assert verdict["device"] == "not_verified"
    assert verdict["incomplete_reasons"] == ["partition sdb3: encrypted (crypto_LUKS), not scanned"]
    partitions = report["device"]["partitions"]  # type: ignore[index]
    assert partitions[2] == {
        "node": "/dev/sdb3",
        "name": "sdb3",
        "filesystem": "",
        "label": "",
        "size": 0,
        "used": 0,
        "scanned": False,
        "not_scanned": "encrypted (crypto_LUKS)",
    }


def test_partition_that_cannot_be_mounted(
    config: Config, display: RecordingDisplay, roots: dict[str, Path], pool: WorkerPool
) -> None:
    mounter = PartitionMounter(roots)
    mounter.refused.add("/dev/sdb1")
    report = run(config, display, mounter, pool)

    # The other partition is scanned and cleaned, the key is not verified
    assert not (roots["/dev/sdb2"] / "docs" / "eicar.com").exists()
    assert display.screens[-1][0] is Screen.NOT_VERIFIED
    assert report["verdict"]["incomplete_reasons"] == [  # type: ignore[index]
        "partition sdb1: cannot mount: cannot mount /dev/sdb1, not scanned"
    ]


def test_no_partition_can_be_mounted(
    config: Config, display: RecordingDisplay, roots: dict[str, Path], pool: WorkerPool
) -> None:
    mounter = PartitionMounter(roots)
    mounter.refused.update(roots)
    Kiosk(config, display, ListSource([DeviceEvent(Action.ADD, KEY)]), pool, mounter).run()
    assert display.screens[-1][0] is Screen.ERROR
    assert "Cannot read any partition of the device" in display.messages
    assert not list(Path(config.report.folder).glob("*.json"))


def test_partition_that_cannot_be_cleaned(
    config: Config, display: RecordingDisplay, roots: dict[str, Path], pool: WorkerPool
) -> None:
    (roots["/dev/sdb1"] / "eicar.com").write_bytes(EICAR)
    mounter = PartitionMounter(roots)
    mounter.refused_rw.add("/dev/sdb2")
    report = run(config, display, mounter, pool)

    # Cleaned where it could be: the other partition keeps its threat
    assert not (roots["/dev/sdb1"] / "eicar.com").exists()
    assert (roots["/dev/sdb2"] / "docs" / "eicar.com").exists()
    assert display.screens[-1][0] is Screen.NOT_CLEANED
    actions = report["actions"]
    assert isinstance(actions, dict)
    assert actions["removed"] == ["sdb1/eicar.com"]
    assert actions["remove_failed"] == ["sdb2/docs/eicar.com"]
    assert actions["cleaned"] is False
