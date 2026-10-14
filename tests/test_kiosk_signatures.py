"""Signature sets in the kiosk: verification at start and update devices."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from pandora_box.config import Config, parse_config
from pandora_box.device import UsbDevice
from pandora_box.engines.fake import EICAR
from pandora_box.kiosk import Kiosk, build_pool, check_signatures, restart_clamd
from pandora_box.monitor import Action, DeviceEvent
from pandora_box.sigsets import UPDATE_FOLDER, SignatureSetError, install, installed_manifest
from pandora_box.ui.base import Screen

from .conftest import DirectoryMounter, ListSource, RecordingDisplay
from .test_sigsets import make_key, make_set

pytestmark = pytest.mark.skipif(not Path("/usr/bin/openssl").exists(), reason="no openssl")

KEY_A = UsbDevice("/dev/sdb1", "vfat", "KEY-A")
KEY_B = UsbDevice("/dev/sdc1", "vfat", "KEY-B")
KEY_C = UsbDevice("/dev/sdd1", "vfat", "KEY-C")
FILES = {"clamav/test.hdb": b"clamav db\n"}


class DevicesMounter(DirectoryMounter):
    """Each device 'mounts' its own directory."""

    def __init__(self, roots: dict[str, Path]) -> None:
        super().__init__(next(iter(roots.values())))
        self.roots = roots

    def mount(self, device: UsbDevice, read_only: bool = True) -> None:
        self.mount_point = self.roots[device.node]
        super().mount(device, read_only)


@pytest.fixture
def key(config: Config) -> Path:
    return make_key(config.signatures.keys, "update")


def data_key(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "eicar.com").write_bytes(EICAR)
    return root


def update_key(tmp_path: Path, name: str, serial: int, signing_key: Path) -> Path:
    root = tmp_path / name
    make_set(root / UPDATE_FOLDER, FILES, serial, signing_key)
    (root / "eicar.com").write_bytes(EICAR)
    return root


def run(
    config: Config,
    display: RecordingDisplay,
    devices: dict[UsbDevice, Path],
    signatures_error: str | None = None,
    start: bool = True,
    remove: bool = False,
) -> None:
    """Insert the devices one after the other (and remove each one: remove)."""
    mounter = DevicesMounter({d.node: p for d, p in devices.items()})
    events = []
    for d in devices:
        events.append(DeviceEvent(Action.ADD, d))
        if remove:
            events.append(DeviceEvent(Action.REMOVE, d))
    pool = build_pool(config)
    if start:
        pool.start()
    try:
        Kiosk(config, display, ListSource(events), pool, mounter, signatures_error).run()
    finally:
        pool.stop()


def test_update_device_is_installed_not_scanned(
    config: Config, display: RecordingDisplay, tmp_path: Path, key: Path
) -> None:
    root = update_key(tmp_path, "update", 1, key)
    run(config, display, {KEY_A: root}, remove=True)
    assert "Signatures updated: set 1" in display.messages
    assert "A signature update device is not scanned. Remove the device." in display.messages
    # The screen of the update stays until the device is removed
    assert [screen for screen, _ in display.screens] == [
        Screen.READY,
        Screen.SCANNING,
        Screen.SERVICE,
        Screen.READY,
    ]
    assert installed_manifest(config.signatures.folder).serial == 1  # type: ignore[union-attr]
    # Not scanned, not cleaned
    assert (root / "eicar.com").exists()
    assert display.confirmations == 0


def test_update_signed_by_another_key_is_refused(
    config: Config, display: RecordingDisplay, tmp_path: Path, key: Path
) -> None:
    foreign = make_key(tmp_path / "foreign", "foreign")
    run(config, display, {KEY_A: update_key(tmp_path, "update", 1, foreign)})
    assert any(
        m.startswith("Signature update REFUSED: invalid signature") for m in display.messages
    )
    assert installed_manifest(config.signatures.folder) is None


def test_older_update_is_not_installed(
    config: Config, display: RecordingDisplay, tmp_path: Path, key: Path
) -> None:
    run(config, display, {KEY_A: update_key(tmp_path, "new", 2, key)})
    run(config, display, {KEY_B: update_key(tmp_path, "old", 1, key)})
    assert any(m.startswith("Signatures already up to date: serial 1") for m in display.messages)
    assert installed_manifest(config.signatures.folder).serial == 2  # type: ignore[union-attr]


def test_kiosk_without_signatures_only_accepts_an_update(
    config: Config, display: RecordingDisplay, tmp_path: Path, key: Path
) -> None:
    before = data_key(tmp_path, "a")
    update = update_key(tmp_path, "u", 1, key)
    after = data_key(tmp_path, "c")
    run(
        config,
        display,
        {KEY_A: before, KEY_B: update, KEY_C: after},
        signatures_error="no signature set installed",
        start=False,
    )
    assert "NO VALID SIGNATURES: no signature set installed" in display.messages
    assert "Cannot scan: no valid signatures. Remove the device." in display.messages
    assert [screen for screen, _ in display.screens][:3] == [
        Screen.UNAVAILABLE,
        Screen.SCANNING,
        Screen.UNAVAILABLE,
    ]
    # Refused before the update, scanned and cleaned after it
    assert (before / "eicar.com").exists()
    assert "Signatures updated: set 1" in display.messages
    assert display.messages.count("Ready. Insert a USB device.") == 1
    assert not (after / "eicar.com").exists()


def signed_engine_config(config: Config) -> Config:
    current = config.signatures.folder / "current"
    return parse_config(
        {
            "kiosk": {"name": "test"},
            "scan": {"sandbox": False},
            "signatures": {
                "folder": str(config.signatures.folder),
                "keys": str(config.signatures.keys),
            },
            "engines": {
                "malwarebazaar": {"database": str(current / "malwarebazaar/mb.bin")},
                "hashlookup": {"enabled": False},
                "yara": {"rules": [{"name": "r", "path": str(current / "yara")}]},
            },
        }
    )


def test_check_signatures(config: Config, tmp_path: Path, key: Path) -> None:
    files = {"malwarebazaar/mb.bin": b"db", "yara/a.yar": b"rule", "clamav/x.hdb": b"x"}
    install(make_set(tmp_path / "set", files, 3, key), config.signatures.folder, [
        config.signatures.keys / "update.pem"
    ])  # fmt: skip
    signed = signed_engine_config(config)
    assert check_signatures(signed).serial == 3  # type: ignore[union-attr]

    outside = dataclasses.replace(
        signed,
        engines=dataclasses.replace(
            signed.engines,
            malwarebazaar=dataclasses.replace(
                signed.engines.malwarebazaar, database=tmp_path / "mb.bin"
            ),
        ),
    )
    with pytest.raises(SignatureSetError, match="is not in the signature set"):
        check_signatures(outside)

    (config.signatures.folder / "current/yara/a.yar").write_bytes(b"rule changed")
    with pytest.raises(SignatureSetError, match="modified"):
        check_signatures(signed)


def test_clamd_is_not_restarted_in_fake_scan_mode(
    config: Config,
    display: RecordingDisplay,
    tmp_path: Path,
    key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The test set has ClamAV files: no restart of a clamd the kiosk does not use."""
    restarts: list[bool] = []
    monkeypatch.setattr("pandora_box.kiosk.restart_clamd", lambda: restarts.append(True))
    run(config, display, {KEY_A: update_key(tmp_path, "update", 1, key)})
    assert "Signatures updated: set 1" in display.messages
    assert restarts == []


def test_restart_clamd_never_asks_for_a_password(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        "pandora_box.kiosk.subprocess.run",
        lambda argv, **kwargs: commands.append(list(argv)),
    )
    monkeypatch.setattr("pandora_box.kiosk.Path.is_dir", lambda self: True)
    monkeypatch.setattr("pandora_box.kiosk.Path.exists", lambda self: True)
    restart_clamd()
    assert commands == [
        ["/usr/bin/systemctl", "--no-ask-password", "try-restart", "clamav-daemon.service"]
    ]


def test_versions_are_shown_at_start(
    config: Config, display: RecordingDisplay, tmp_path: Path, key: Path
) -> None:
    from pandora_box import __version__

    install(make_set(tmp_path / "set", FILES, 7, key), config.signatures.folder,
            [config.signatures.keys / "update.pem"])  # fmt: skip
    created = installed_manifest(config.signatures.folder).created  # type: ignore[union-attr]
    run(config, display, {KEY_A: data_key(tmp_path, "data")})
    assert display.messages[0] == f"PandoraBox {__version__} (development)"
    assert display.messages[1] == f"Signatures: set 7 of {created:%Y-%m-%d %H:%M} UTC"


def test_image_version_is_shown_at_start(
    config: Config, display: RecordingDisplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On an image: its version, the date and time of its build
    monkeypatch.setattr("pandora_box.imageupdate.running_version", lambda: 20261005134734)
    run(config, display, {KEY_A: data_key(tmp_path, "data")})
    assert display.messages[0] == "PandoraBox 20261005134734 of 2026-10-05 13:47 UTC"
