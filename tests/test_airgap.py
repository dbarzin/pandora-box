"""Copy of the clean files of a verified key to a server (airgap): kiosk and service."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pandora_box import airgap
from pandora_box.airgap import AirgapError, Staged, Transfer
from pandora_box.config import AirgapConfig, Config, ConfigError, parse_config
from pandora_box.device import UsbDevice
from pandora_box.keyid import TOKEN_FILE, generate_key, new_token, read_token, verify_token
from pandora_box.kiosk import Kiosk, build_pool
from pandora_box.logs import redact_key_fields
from pandora_box.monitor import Action, DeviceEvent
from pandora_box.remotes import SyncResult
from pandora_box.ui.base import Screen
from pandora_box.workers import WorkerPool

from .conftest import DirectoryMounter, ListSource, RecordingDisplay
from .samples import eicar

EICAR = eicar()

DEVICE = UsbDevice("/dev/sdb1", "vfat", "KEY")
# Name of the folder of a key on the server (pandora_box.keyid.folder_of)
KEY_ID = "5f" * 32
REPORT_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"

# -- configuration ---------------------------------------------------------------------


def test_airgap_config() -> None:
    assert parse_config({}).airgap.enabled is False
    config = parse_config(
        {
            "airgap": {
                "enabled": True,
                "protocol": "rsync",
                "host": "files.example.org",
                "user": "kiosk",
                "path": "/srv/keys",
            }
        }
    ).airgap
    assert config.protocol == "rsync" and config.path == "/srv/keys"


@pytest.mark.parametrize(
    ("airgap_table", "error"),
    [
        ({"enabled": True, "user": "u", "path": "p"}, "airgap.host is required"),
        ({"protocol": "smb"}, "must be one of"),
        ({"host": "user@host"}, "invalid characters"),
        ({"path": "keys/../etc"}, "must not hold .."),
        ({"share": "data"}, "unknown key"),
        ({"protocol": "rsync", "windows_names": True}, 'only used with protocol = "sftp"'),
        ({"port": 70000}, "between 1 and 65535"),
        ({"password": "secret"}, "unknown key"),
    ],
)
def test_invalid_airgap_config(airgap_table: dict[str, Any], error: str) -> None:
    with pytest.raises(ConfigError, match=error):
        parse_config({"airgap": airgap_table})


# -- staging ---------------------------------------------------------------------------


def test_staging(tmp_path: Path) -> None:
    config = AirgapConfig(staging=tmp_path / "staging")
    root = tmp_path / "key"
    root.mkdir()
    (root / "a.txt").write_bytes(b"clean")
    (root / "b.txt").write_bytes(b"changed after the scan")
    staged = airgap.prepare(config, KEY_ID, 100)
    airgap.stage_file(staged, root, "a.txt", "a.txt", hashlib.sha256(b"clean").hexdigest())
    airgap.stage_file(staged, root, "b.txt", "b.txt", hashlib.sha256(b"scanned").hexdigest())
    airgap.stage_file(staged, root, "gone.txt", "gone.txt", "0" * 64)
    assert staged.files == ["a.txt"]
    assert dict(staged.errors)["b.txt"] == "changed since the scan"
    assert "gone.txt" in dict(staged.errors)
    assert (staged.folder / "files/a.txt").read_bytes() == b"clean"
    assert not (staged.folder / "files/b.txt").exists()
    airgap.remove_staging(config, KEY_ID)
    assert not staged.folder.exists()


def test_staging_needs_room_and_a_valid_identifier(tmp_path: Path) -> None:
    config = AirgapConfig(staging=tmp_path / "staging")
    with pytest.raises(AirgapError, match="not enough space"):
        airgap.prepare(config, KEY_ID, 1 << 62)
    with pytest.raises(AirgapError, match="invalid key folder"):
        airgap.prepare(config, "../../etc", 1)


# -- service and client ----------------------------------------------------------------


@pytest.fixture
def service_config() -> Iterator[AirgapConfig]:
    # Short path: Unix socket paths are limited to 108 bytes
    folder = Path(tempfile.mkdtemp(prefix="airgap-"))
    yield AirgapConfig(
        enabled=True,
        host="h",
        user="u",
        path="/srv/keys",
        staging=folder / "staging",
        socket=folder / "airgap.sock",
        timeout=5,
    )
    shutil.rmtree(folder, ignore_errors=True)


def start_service(
    config: AirgapConfig, transfer_key: Callable[..., tuple[SyncResult, str]]
) -> None:
    thread = threading.Thread(target=airgap.serve, args=(config, transfer_key), daemon=True)
    thread.start()
    for _ in range(100):
        if config.socket.exists():
            return
        threading.Event().wait(0.02)


def test_service_copies_the_key(service_config: AirgapConfig) -> None:
    calls: list[tuple[str, str]] = []

    def transfer_key(
        config: AirgapConfig, key: str, report_id: str, progress: Callable[[int, int], None]
    ) -> tuple[SyncResult, str]:
        calls.append((key, report_id))
        progress(5, 10)
        progress(10, 10)
        result = SyncResult(copied=["a.txt"], deleted=["old.txt"], unchanged=3)
        result.errors.append(("bad?.txt", "name refused"))
        return result, f"/srv/keys/{key}"

    start_service(service_config, transfer_key)
    seen: list[tuple[int, int]] = []
    transfer = airgap.request(
        service_config, KEY_ID, REPORT_ID, lambda done, total: seen.append((done, total))
    )
    assert calls == [(KEY_ID, REPORT_ID)]
    assert seen == [(5, 10), (10, 10)]
    assert transfer == Transfer(
        copied=1,
        deleted=1,
        unchanged=3,
        errors=[("bad?.txt", "name refused")],
        destination=f"/srv/keys/{KEY_ID}",
    )


def test_service_failure(service_config: AirgapConfig) -> None:
    def transfer_key(*args: Any) -> tuple[SyncResult, str]:
        raise AirgapError("sftp: [Errno 113] No route to host")

    start_service(service_config, transfer_key)
    with pytest.raises(AirgapError, match="No route to host"):
        airgap.request(service_config, KEY_ID, REPORT_ID)


def test_service_unavailable(service_config: AirgapConfig) -> None:
    with pytest.raises(AirgapError, match="copy service unavailable"):
        airgap.request(service_config, KEY_ID, REPORT_ID)


def test_transfer_checks_the_request(service_config: AirgapConfig) -> None:
    with pytest.raises(AirgapError, match="invalid key folder"):
        airgap.transfer(service_config, "../x", REPORT_ID)
    with pytest.raises(AirgapError, match="nothing staged"):
        airgap.transfer(service_config, KEY_ID, REPORT_ID)
    staged = airgap.prepare(service_config, KEY_ID, 1)
    (staged.folder / airgap.REPORT).write_text("{}")
    with pytest.raises(AirgapError, match="invalid report identifier"):
        airgap.transfer(service_config, KEY_ID, "../../x")

    def upload(files: Path, report: Path, server: Any, secrets: Any, remote: str, name: str,
               progress: Any) -> SyncResult:  # fmt: skip
        assert files == staged.folder / "files" and name == f"{REPORT_ID}.json"
        assert remote == f"/srv/keys/{KEY_ID}"
        return SyncResult(copied=["x"])

    result, remote = airgap.transfer(service_config, KEY_ID, REPORT_ID, upload_files=upload)
    assert result.copied == ["x"] and remote == f"/srv/keys/{KEY_ID}"


# -- kiosk -----------------------------------------------------------------------------


@pytest.fixture
def token_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    key = credentials / airgap.TOKEN_KEY_CREDENTIAL
    generate_key(key)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))
    return key


@pytest.fixture
def airgap_config(config: Config, tmp_path: Path) -> Config:
    return dataclasses.replace(
        config,
        airgap=AirgapConfig(
            enabled=True, host="h", user="u", path="/srv/keys", staging=tmp_path / "staging"
        ),
    )


@pytest.fixture
def pool(airgap_config: Config) -> Iterator[WorkerPool]:
    with build_pool(airgap_config) as pool:
        yield pool


class FakeService:
    """The copy service: records what the kiosk staged, serves the files of the server.

    outbound: the files waiting on the server for the key, path: (content, time).
    """

    def __init__(self, error: str = "", outbound: dict[str, tuple[bytes, int]] | None = None):
        self.error = error
        self.outbound = outbound or {}
        self.staged: dict[str, bytes] = {}
        self.report: dict[str, Any] = {}
        self.keys: list[str] = []
        self.operations: list[str] = []

    def __call__(
        self,
        config: AirgapConfig,
        key: str,
        report_id: str,
        progress: Callable[[int, int], None] | None = None,
        operation: str = "inbound",
    ) -> Transfer:
        folder = config.staging / key
        self.operations.append(operation)
        if operation == "report":
            self.report = json.loads((folder / "report.json").read_text())
            return Transfer()
        if operation == "outbound":
            have = json.loads((folder / airgap.KEY_FILES).read_text())
            fetched = unchanged = 0
            for path, (content, mtime) in self.outbound.items():
                if have.get(path) == [len(content), mtime]:
                    unchanged += 1
                    continue
                target = folder / airgap.OUTBOUND / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                os.utime(target, (mtime, mtime))
                fetched += 1
            return Transfer(fetched=fetched, unchanged=unchanged)
        files = folder / "files"
        self.staged = {
            p.relative_to(files).as_posix(): p.read_bytes() for p in files.rglob("*") if p.is_file()
        }
        self.report = json.loads((folder / "report.json").read_text())
        self.keys.append(key)
        if self.error:
            raise AirgapError(self.error)
        if progress is not None:
            progress(10, 10)
        return Transfer(copied=len(self.staged), destination=f"/srv/keys/{key}")


def run_kiosk(
    config: Config, display: RecordingDisplay, root: Path, pool: WorkerPool
) -> DirectoryMounter:
    mounter = DirectoryMounter(root)
    Kiosk(config, display, ListSource([DeviceEvent(Action.ADD, DEVICE)]), pool, mounter).run()
    return mounter


def reports(config: Config) -> list[Path]:
    """The reports kept by the kiosk."""
    return sorted(config.report.folder.glob("*.json"))


def test_key_copied_to_the_server(
    airgap_config: Config,
    display: RecordingDisplay,
    usb_tree: Path,
    pool: WorkerPool,
    token_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeService()
    monkeypatch.setattr(airgap, "request", service)
    run_kiosk(airgap_config, display, usb_tree, pool)

    # The key gets its identifier, and is not cleaned
    identity = verify_token(read_token(usb_tree), token_key)
    assert (usb_tree / "docs" / "eicar.com").exists()
    assert display.confirmations == 0
    # Only the clean files are staged, never the token or the malicious file
    assert service.keys == [identity.folder]
    assert service.staged == {"readme.txt": b"hello", "docs/report.pdf": b"%PDF-1.4 harmless"}
    assert service.report["actions"]["airgap"]["key_id"] == identity.identifier
    # The staging folder is deleted
    assert not (airgap_config.airgap.staging / identity.folder).exists()
    screens = [screen for screen, _ in display.screens]
    assert screens[-2:] == [Screen.TRANSFERRING, Screen.TRANSFERRED]
    assert display.screens[-1][1] == ["docs/eicar.com"]
    # The report goes to the server only: the station keeps nothing of the key
    assert service.report["actions"]["cleaned"] is None
    assert service.report["actions"]["airgap"]["not_copied"] == ["docs/eicar.com"]
    assert not reports(airgap_config)
    assert not airgap_config.quarantine.folder.exists()

    # Inserted again: the same identifier, the token is not written again
    token = (usb_tree / TOKEN_FILE).read_bytes()
    run_kiosk(airgap_config, RecordingDisplay(), usb_tree, pool)
    assert service.keys == [identity.folder, identity.folder]
    assert (usb_tree / TOKEN_FILE).read_bytes() == token


def test_invalid_token_is_replaced(
    airgap_config: Config,
    display: RecordingDisplay,
    usb_tree: Path,
    pool: WorkerPool,
    token_key: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = tmp_path / "other.key"
    generate_key(other)
    foreign, token = new_token(other)
    (usb_tree / TOKEN_FILE).write_bytes(token)
    service = FakeService()
    monkeypatch.setattr(airgap, "request", service)
    run_kiosk(airgap_config, display, usb_tree, pool)
    identity = verify_token(read_token(usb_tree), token_key)
    assert identity.identifier != foreign.identifier and service.keys == [identity.folder]


def test_unverified_key_is_not_copied(
    airgap_config: Config,
    display: RecordingDisplay,
    tmp_path: Path,
    pool: WorkerPool,
    token_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "big.iso").write_bytes(b"x" * 100)
    config = dataclasses.replace(
        airgap_config, limits=dataclasses.replace(airgap_config.limits, max_file_size=10)
    )
    service = FakeService()
    monkeypatch.setattr(airgap, "request", service)
    run_kiosk(config, display, root, pool)
    assert service.keys == []
    assert not (root / TOKEN_FILE).exists()
    assert display.screens[-1][0] is Screen.NOT_VERIFIED
    assert not reports(config)


def test_copy_failure(
    airgap_config: Config,
    display: RecordingDisplay,
    usb_tree: Path,
    pool: WorkerPool,
    token_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeService(error="sftp: [Errno 113] No route to host")
    monkeypatch.setattr(airgap, "request", service)
    run_kiosk(airgap_config, display, usb_tree, pool)
    assert display.screens[-1][0] is Screen.TRANSFER_FAILED
    assert "Copy FAILED: sftp: [Errno 113] No route to host" in display.messages
    assert not reports(airgap_config)
    assert not any(airgap_config.airgap.staging.iterdir())


def test_no_token_key(
    airgap_config: Config,
    display: RecordingDisplay,
    usb_tree: Path,
    pool: WorkerPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setattr(airgap, "request", FakeService())
    run_kiosk(airgap_config, display, usb_tree, pool)
    assert display.screens[-1][0] is Screen.TRANSFER_FAILED
    assert not (usb_tree / TOKEN_FILE).exists()


def test_station_logs_nothing_of_the_keys(
    airgap_config: Config,
    display: RecordingDisplay,
    usb_tree: Path,
    pool: WorkerPool,
    token_key: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No name of file, label, serial number or folder of the key in the log."""
    service = FakeService()
    monkeypatch.setattr(airgap, "request", service)
    device = UsbDevice("/dev/sdb1", "vfat", "SECRET-LABEL", "Model", "SERIAL-42", "Vendor")
    redact_key_fields(True)
    try:
        with caplog.at_level(logging.DEBUG, logger="pandora_box"):
            Kiosk(
                airgap_config, display, ListSource([DeviceEvent(Action.ADD, device)]), pool,
                DirectoryMounter(usb_tree),
            ).run()  # fmt: skip
    finally:
        redact_key_fields(False)
    identity = verify_token(read_token(usb_tree), token_key)
    logged = json.dumps([getattr(r, "fields", {}) for r in caplog.records] + caplog.messages)
    for secret in ("SECRET-LABEL", "SERIAL-42", "readme.txt", "eicar.com", identity.folder):
        assert secret not in logged
    assert identity.identifier not in logged


@pytest.fixture
def clean_key(tmp_path: Path) -> Path:
    root = tmp_path / "media"
    (root / "docs").mkdir(parents=True)
    (root / "readme.txt").write_text("hello")
    os.utime(root / "readme.txt", (1_700_000_000, 1_700_000_000))
    return root


def test_files_of_the_server_copied_to_the_key(
    airgap_config: Config,
    display: RecordingDisplay,
    clean_key: Path,
    pool: WorkerPool,
    token_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New and changed files of the server are scanned, the clean ones written at the root."""
    service = FakeService(
        outbound={
            "readme.txt": (b"hello", 1_700_000_000),  # already on the key
            "docs/answer.pdf": (b"%PDF-1.4 answer", 1_700_000_100),
            "virus.com": (EICAR, 1_700_000_200),
            ".pandora-box": (b"forged token", 1_700_000_300),
        }
    )
    monkeypatch.setattr(airgap, "request", service)
    run_kiosk(airgap_config, display, clean_key, pool)

    assert service.operations == ["inbound", "outbound", "report"]
    assert (clean_key / "docs" / "answer.pdf").read_bytes() == b"%PDF-1.4 answer"
    assert (clean_key / "docs" / "answer.pdf").stat().st_mtime == 1_700_000_100
    # The malicious file of the server is not written, nor the token replaced
    assert not (clean_key / "virus.com").exists()
    verify_token(read_token(clean_key), token_key)
    assert display.screens[-1][0] is Screen.TRANSFERRED
    outbound = service.report["actions"]["airgap"]["outbound"]
    assert outbound["status"] == "copied" and outbound["copied"] == ["docs/answer.pdf"]
    assert outbound["unchanged"] == 1
    reasons = {r["path"]: r["reason"] for r in outbound["rejected"]}
    assert reasons["virus.com"].startswith("malicious")
    assert "reserved" in reasons[".pandora-box"]
    assert not list(clean_key.rglob("*.pandora-box-tmp"))


def test_dangerous_key_gets_no_file_of_the_server(
    airgap_config: Config,
    display: RecordingDisplay,
    usb_tree: Path,
    pool: WorkerPool,
    token_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key that holds an EICAR file is refused until it is cleaned."""
    service = FakeService(outbound={"answer.txt": (b"answer", 1_700_000_100)})
    monkeypatch.setattr(airgap, "request", service)
    run_kiosk(airgap_config, display, usb_tree, pool)
    assert "outbound" not in service.operations
    assert not (usb_tree / "answer.txt").exists()
    assert service.report["actions"]["airgap"]["outbound"]["status"] == "refused"


def test_outbound_station_only(
    airgap_config: Config,
    display: RecordingDisplay,
    clean_key: Path,
    pool: WorkerPool,
    token_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = dataclasses.replace(
        airgap_config, airgap=dataclasses.replace(airgap_config.airgap, mode="outbound")
    )
    service = FakeService(outbound={"answer.txt": (b"answer", 1_700_000_100)})
    monkeypatch.setattr(airgap, "request", service)
    run_kiosk(config, display, clean_key, pool)
    assert service.operations == ["outbound", "report"]
    assert (clean_key / "answer.txt").read_bytes() == b"answer"
    assert service.report["actions"]["airgap"]["status"] == "skipped"


def test_names_a_fat_key_cannot_hold(
    airgap_config: Config,
    display: RecordingDisplay,
    clean_key: Path,
    pool: WorkerPool,
    token_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeService(
        outbound={
            "what?.txt": (b"1", 1_700_000_100),
            "Answer.txt": (b"2", 1_700_000_100),
            "answer.txt": (b"3", 1_700_000_100),
        }
    )
    monkeypatch.setattr(airgap, "request", service)
    run_kiosk(airgap_config, display, clean_key, pool)  # DEVICE is vfat
    outbound = service.report["actions"]["airgap"]["outbound"]
    assert outbound["copied"] == ["Answer.txt"]
    reasons = {r["path"]: r["reason"] for r in outbound["rejected"]}
    assert "refuses" in reasons["what?.txt"] and "case ignored" in reasons["answer.txt"]
    assert not (clean_key / "what?.txt").exists()


def cleaning(config: Config, quarantine: bool = True) -> Config:
    return dataclasses.replace(
        config,
        airgap=dataclasses.replace(config.airgap, clean=True),
        quarantine=dataclasses.replace(config.quarantine, enabled=quarantine),
    )


def test_station_that_cleans(
    airgap_config: Config,
    display: RecordingDisplay,
    usb_tree: Path,
    pool: WorkerPool,
    token_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """airgap.clean: copied, cleaned after a touch, then the files of the server come."""
    config = cleaning(airgap_config)
    service = FakeService(outbound={"answer.txt": (b"answer", 1_700_000_100)})
    monkeypatch.setattr(airgap, "request", service)
    run_kiosk(config, display, usb_tree, pool)
    assert service.operations == ["inbound", "outbound", "report"]
    assert display.confirmations == 1
    assert not (usb_tree / "docs" / "eicar.com").exists()
    assert (usb_tree / "answer.txt").read_bytes() == b"answer"
    actions = service.report["actions"]
    assert actions["cleaned"] is True and actions["removed"] == ["docs/eicar.com"]
    assert actions["airgap"]["outbound"]["status"] == "copied"
    # A copy of the removed file in the quarantine (quarantine.enabled)
    assert list(config.quarantine.folder.glob("*/manifest.json"))
    assert display.screens[-1][0] is Screen.TRANSFERRED


def test_station_that_cleans_without_quarantine(
    airgap_config: Config,
    display: RecordingDisplay,
    usb_tree: Path,
    pool: WorkerPool,
    token_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = cleaning(airgap_config, quarantine=False)
    monkeypatch.setattr(airgap, "request", FakeService())
    run_kiosk(config, display, usb_tree, pool)
    assert not (usb_tree / "docs" / "eicar.com").exists()
    assert not config.quarantine.folder.exists()


def test_key_removed_before_cleaning(
    airgap_config: Config,
    display: RecordingDisplay,
    usb_tree: Path,
    pool: WorkerPool,
    token_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeService(outbound={"answer.txt": (b"answer", 1_700_000_100)})
    monkeypatch.setattr(airgap, "request", service)
    mounter = DirectoryMounter(usb_tree)
    original = display.confirm

    def remove_key(prompt: str) -> None:
        mounter.present = False
        original(prompt)

    display.confirm = remove_key  # type: ignore[method-assign]
    Kiosk(
        cleaning(airgap_config), display, ListSource([DeviceEvent(Action.ADD, DEVICE)]), pool,
        mounter,
    ).run()  # fmt: skip
    assert service.operations == ["inbound"]
    assert (usb_tree / "docs" / "eicar.com").exists()
    assert display.screens[-1][0] is Screen.TRANSFER_FAILED
    assert "Copy FAILED: key removed before cleaning" in display.messages


def test_staged_type() -> None:
    assert Staged(Path("/x")).files == []
