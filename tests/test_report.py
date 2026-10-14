from __future__ import annotations

import base64
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from pandora_box.config import Config
from pandora_box.device import UsbDevice
from pandora_box.engines import EngineKind, EngineResult, FileInfo, SignatureInfo, Verdict
from pandora_box.kiosk import Kiosk
from pandora_box.monitor import Action, DeviceEvent
from pandora_box.policy import KNOWN_FILE
from pandora_box.report import DeviceUsage, build_report, load_schema, write_report
from pandora_box.results import FileResult, ScanSummary
from pandora_box.workers import EngineInfo, WorkerPool

from .conftest import DirectoryMounter, ListSource, RecordingDisplay

DEVICE = UsbDevice("/dev/sdb1", "vfat", "KEY\x1b[2J", "Model", "SN-1", "Vendor")


def validate(report: dict[str, Any]) -> None:
    jsonschema.validate(report, load_schema(), format_checker=jsonschema.FormatChecker())


def run_kiosk(config: Config, root: Path, pool: WorkerPool) -> tuple[dict[str, Any], Path]:
    display = RecordingDisplay()
    source = ListSource([DeviceEvent(Action.ADD, DEVICE)])
    Kiosk(config, display, source, pool, DirectoryMounter(root)).run()
    [path] = list(config.report.folder.glob("*.json"))
    return json.loads(path.read_text()), path


def test_schema_is_valid() -> None:
    jsonschema.Draft202012Validator.check_schema(load_schema())


def test_report_of_a_kiosk_scan(config: Config, usb_tree: Path, fake_pool: WorkerPool) -> None:
    hostile = os.fsdecode(b"bad-\xff\x1b[31m.txt")
    (usb_tree / hostile).write_text("x")
    report, path = run_kiosk(config, usb_tree, fake_pool)
    validate(report)

    # Written with restrictive permissions under a timestamped name
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert path.name.endswith(f"-{report['report_id'][:8]}.json")
    assert path.name[:16] == report["started"].replace("-", "").replace(":", "")[:15] + "Z"

    assert report["kiosk"]["name"] == "test"
    assert report["device"]["label"] == "KEY\\x1b[2J"
    assert report["device"]["filesystem"] == "vfat"
    assert report["configuration"]["scan"]["on_error"] == "block"
    assert report["engines"][0]["name"] == "fake"

    files = {f["path"]: f for f in report["files"]}
    eicar = files["docs/eicar.com"]
    assert eicar["verdict"] == "malicious"
    assert eicar["engines"][0]["detections"] == ["EICAR-Test-File"]
    assert len(eicar["sha256"]) == 64 and eicar["mime"]
    bad = files["bad-\\xff\\x1b[31m.txt"]
    assert base64.b64decode(bad["path_b64"]) == b"bad-\xff\x1b[31m.txt"

    verdict = report["verdict"]
    assert verdict["device"] == "malicious"
    assert verdict["complete"] is True
    assert verdict["statistics"]["files"] == 4
    assert verdict["statistics"]["malicious"] == 1

    # Updated after cleaning
    actions = report["actions"]
    assert actions["removed"] == ["docs/eicar.com"]
    assert actions["quarantined"] == ["docs/eicar.com"]
    assert actions["cleaned"] is True

    # The quarantine manifest references the report
    manifest_path = Path(actions["quarantine_folder"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["report"] == {"id": report["report_id"], "path": str(path)}
    assert manifest["files"][0]["original_path"] == "docs/eicar.com"


def test_report_of_a_clean_device(config: Config, tmp_path: Path, fake_pool: WorkerPool) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "a.txt").write_text("a")
    report, _ = run_kiosk(config, root, fake_pool)
    validate(report)
    assert report["verdict"]["device"] == "clean"
    assert report["actions"]["cleaned"] is None
    assert report["actions"]["quarantine_folder"] is None


def test_report_failure_is_not_fatal(
    config: Config, usb_tree: Path, tmp_path: Path, fake_pool: WorkerPool
) -> None:
    (tmp_path / "reports").write_text("not a folder")
    display = RecordingDisplay()
    source = ListSource([DeviceEvent(Action.ADD, DEVICE)])
    Kiosk(config, display, source, fake_pool, DirectoryMounter(usb_tree)).run()
    assert any(m.startswith("WARNING: cannot write the scan report") for m in display.messages)
    assert "Device cleaned! You can remove the device." in display.messages


def test_report_records_known_files_and_errors(tmp_path: Path, config: Config) -> None:
    info = FileInfo("lib.dll", 3, "a" * 64, "b" * 40, "c" * 32, "application/x-dosexec", "PE32")
    known = FileResult(
        tmp_path / "lib.dll",
        3,
        Verdict.CLEAN,
        (
            EngineResult("hashlookup", Verdict.CLEAN, facts={"known": True, "fp_rate": 0.0001}),
            EngineResult("clamav", Verdict.SKIPPED, reason=KNOWN_FILE),
        ),
        detail=KNOWN_FILE,
        info=info,
        rel_path="lib.dll",
    )
    big = FileResult(
        tmp_path / "big.iso",
        10,
        Verdict.SKIPPED,
        detail="too big",
        rel_path="big.iso",
        incomplete=True,
    )
    summary = ScanSummary([known, big], 1.5, ["more than 10 files"])
    engines = [
        EngineInfo(
            "clamav",
            EngineKind.CONTENT,
            "ClamAV 1.4.3",
            120.0,
            (SignatureInfo("clamav", "27791", datetime(2026, 10, 2, tzinfo=UTC)),),
        ),
        EngineInfo(
            "yara",
            EngineKind.CONTENT,
            "YARA-X 1.21.0",
            60.0,
            (),
            {"excluded_rules": ["a.yar:1: E009"]},
        ),
    ]
    now = datetime.now(UTC)
    report = build_report(config, DEVICE, DeviceUsage(100, 50), engines, summary, now, now, 2)
    path = write_report(report, tmp_path / "reports")
    data = json.loads(path.read_text())
    validate(data)
    files = {f["path"]: f for f in data["files"]}
    assert files["lib.dll"]["detail"] == KNOWN_FILE
    assert files["lib.dll"]["engines"][0]["facts"] == {"known": True, "fp_rate": 0.0001}
    assert files["big.iso"]["not_fully_scanned"] is True
    assert files["big.iso"]["sha256"] is None
    assert data["verdict"]["device"] == "not_verified"
    assert data["verdict"]["incomplete_reasons"] == ["more than 10 files"]
    assert data["engines"][0]["signatures"][0]["date"] == "2026-10-02T00:00:00Z"
    assert data["engines"][1]["extra"]["excluded_rules"] == ["a.yar:1: E009"]


def test_invalid_report_is_rejected(config: Config) -> None:
    now = datetime.now(UTC)
    report = build_report(config, DEVICE, DeviceUsage(), [], ScanSummary(), now, now, 2)
    report.data["verdict"]["device"] = "probably fine"
    with pytest.raises(jsonschema.ValidationError):
        validate(report.data)
