"""Maintenance devices: the kiosk exports its logs to a signed USB key."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pandora_box import maintenance
from pandora_box.config import Config
from pandora_box.device import UsbDevice
from pandora_box.engines.fake import EICAR
from pandora_box.maintenance import FOLDER, REQUEST, MaintenanceError, export, request, verify
from pandora_box.sigsets import main

from .conftest import RecordingDisplay
from .test_kiosk_signatures import FILES, run
from .test_sigsets import make_key, make_set

pytestmark = pytest.mark.skipif(not Path("/usr/bin/openssl").exists(), reason="no openssl")

KEY = UsbDevice("/dev/sdb1", "vfat", "MAINTENANCE")


@pytest.fixture
def key(config: Config) -> Path:
    return make_key(config.signatures.keys, "update")


@pytest.fixture
def harmless(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Commands and files of the export that exist everywhere."""
    log = tmp_path / "kiosk.log"
    log.write_text('{"event": "kiosk_started"}\n')
    monkeypatch.setattr(maintenance, "COMMANDS", (("echo.txt", ("/bin/echo", "hello")),))
    files = (("pandora-box.log", str(log)), ("missing.txt", str(tmp_path / "missing")))
    monkeypatch.setattr(maintenance, "FILES", files)
    return log


def test_request_then_export(tmp_path: Path, config: Config, key: Path, harmless: Path) -> None:
    folder = tmp_path / "key" / FOLDER
    request(folder, key)
    verify(folder, config)
    target, count = export(folder, config)
    assert target.parent == folder and target.name.startswith(f"{config.kiosk.name}-")
    assert (target / "echo.txt").read_text() == "hello\n"
    assert (target / "pandora-box.log").read_text() == harmless.read_text()
    assert "No such file" in (target / "missing.txt").read_text()
    assert "no signature set installed" in (target / "signatures.txt").read_text()
    assert (target / "hardware.txt").exists()
    assert count == 5
    # The request is still valid with the export next to it
    verify(folder, config)


def test_a_new_request_ignores_the_previous_exports(
    tmp_path: Path, config: Config, key: Path, harmless: Path
) -> None:
    folder = tmp_path / "key" / FOLDER
    request(folder, key)
    export(folder, config)
    manifest = request(folder, key)
    assert [f.path for f in manifest.files] == [REQUEST]


def test_refused_requests(tmp_path: Path, config: Config, key: Path) -> None:
    folder = tmp_path / "key" / FOLDER
    request(folder, key)
    created = verify(folder, config).created
    with pytest.raises(MaintenanceError, match="valid 7 days"):
        verify(folder, config, now=created + timedelta(days=8))
    with pytest.raises(MaintenanceError, match="valid 7 days"):
        verify(folder, config, now=created - timedelta(days=2))
    # Another kiosk
    request(folder, key, kiosk="another-kiosk")
    with pytest.raises(MaintenanceError, match="another-kiosk"):
        verify(folder, config)
    # Modified after signing
    request(folder, key)
    (folder / REQUEST).write_text(json.dumps({"action": "export-logs", "kiosk": "x"}) + "\n")
    with pytest.raises(MaintenanceError, match="does not match"):
        verify(folder, config)
    # Signed with another key
    request(folder, make_key(tmp_path / "foreign", "foreign"))
    with pytest.raises(MaintenanceError, match="invalid signature"):
        verify(folder, config)
    # A signature set is not a maintenance request
    signatures = make_set(tmp_path / "set", {REQUEST: b"{}"}, 1, key)
    with pytest.raises(MaintenanceError, match="not a manifest of maintenance"):
        verify(signatures, config)


def test_command_line(tmp_path: Path, config: Config, key: Path) -> None:
    folder = tmp_path / "key" / FOLDER
    assert main(["maintenance", str(folder), "--key", str(key), "--kiosk", "k1"]) == 0
    assert json.loads((folder / REQUEST).read_text()) == {"action": "export-logs", "kiosk": "k1"}


def test_kiosk_exports_its_logs_and_does_not_scan(
    tmp_path: Path, config: Config, display: RecordingDisplay, key: Path, harmless: Path
) -> None:
    root = tmp_path / "device"
    request(root / FOLDER, key)
    (root / "eicar.com").write_bytes(EICAR)
    # Without signatures too: the kiosk can be diagnosed
    run(config, display, {KEY: root}, signatures_error="no signature set", start=False)
    exports = [p for p in (root / FOLDER).iterdir() if p.is_dir() and p.name != "request"]
    assert len(exports) == 1 and (exports[0] / "pandora-box.log").exists()
    assert f"Logs exported to {FOLDER}/{exports[0].name}." in display.messages
    assert "A maintenance device is not scanned. Remove the device." in display.messages
    assert (root / "eicar.com").exists()
    assert display.confirmations == 0


def test_kiosk_refuses_an_old_request(
    tmp_path: Path, config: Config, display: RecordingDisplay, key: Path, harmless: Path
) -> None:
    root = tmp_path / "device"
    folder = root / FOLDER
    request(folder, key)
    manifest = json.loads((folder / "manifest.json").read_text())
    old = datetime.now(UTC) - timedelta(days=30)
    from pandora_box.sigsets import build, sign

    build(folder, created=old.replace(microsecond=0), content="maintenance", only=[REQUEST])
    sign(folder, key)
    assert manifest["serial"] != json.loads((folder / "manifest.json").read_text())["serial"]
    run(config, display, {KEY: root}, start=False)
    assert any(m.startswith("Maintenance REFUSED: request of") for m in display.messages)
    assert [p.name for p in folder.iterdir() if p.is_dir()] == ["request"]


def test_one_device_exports_the_logs_and_updates_the_signatures(
    tmp_path: Path, config: Config, display: RecordingDisplay, key: Path, harmless: Path
) -> None:
    from pandora_box.sigsets import UPDATE_FOLDER, installed_manifest

    root = tmp_path / "device"
    request(root / FOLDER, key)
    make_set(root / UPDATE_FOLDER, FILES, 3, key)
    run(config, display, {KEY: root}, signatures_error="no signature set", start=False)
    assert any(m.startswith(f"Logs exported to {FOLDER}/") for m in display.messages)
    assert "Signatures updated: set 3" in display.messages
    assert installed_manifest(config.signatures.folder).serial == 3  # type: ignore[union-attr]
    # The export holds the state before the update
    export = next(p for p in (root / FOLDER).iterdir() if p.is_dir() and p.name != "request")
    assert "no signature set installed" in (export / "signatures.txt").read_text()
