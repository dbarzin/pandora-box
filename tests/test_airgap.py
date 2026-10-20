"""Copy of the clean files of a verified key to a server (airgap): kiosk and service."""

from __future__ import annotations

import dataclasses
import hashlib
import json
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
from pandora_box.monitor import Action, DeviceEvent
from pandora_box.remotes import SyncResult
from pandora_box.ui.base import Screen
from pandora_box.workers import WorkerPool

from .conftest import DirectoryMounter, ListSource, RecordingDisplay

DEVICE = UsbDevice("/dev/sdb1", "vfat", "KEY")
KEY_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
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
    with pytest.raises(AirgapError, match="invalid key identifier"):
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
    with pytest.raises(AirgapError, match="invalid key identifier"):
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
    """The copy service: records what the kiosk staged."""

    def __init__(self, error: str = "") -> None:
        self.error = error
        self.staged: dict[str, bytes] = {}
        self.report: dict[str, Any] = {}
        self.keys: list[str] = []

    def __call__(
        self,
        config: AirgapConfig,
        key: str,
        report_id: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> Transfer:
        folder = config.staging / key
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


def last_report(config: Config) -> dict[str, Any]:
    reports = sorted(config.report.folder.glob("*.json"))
    data: dict[str, Any] = json.loads(reports[-1].read_text())
    return data


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
    identifier = verify_token(read_token(usb_tree), token_key)
    assert (usb_tree / "docs" / "eicar.com").exists()
    assert display.confirmations == 0
    # Only the clean files are staged, never the token or the malicious file
    assert service.keys == [identifier]
    assert service.staged == {"readme.txt": b"hello", "docs/report.pdf": b"%PDF-1.4 harmless"}
    assert service.report["actions"]["airgap"]["key_id"] == identifier
    # The staging folder is deleted
    assert not (airgap_config.airgap.staging / identifier).exists()
    screens = [screen for screen, _ in display.screens]
    assert screens[-2:] == [Screen.TRANSFERRING, Screen.TRANSFERRED]
    assert display.screens[-1][1] == ["docs/eicar.com"]
    action = last_report(airgap_config)["actions"]["airgap"]
    assert action["status"] == "copied"
    assert action["copied"] == 2 and action["not_copied"] == ["docs/eicar.com"]
    assert action["destination"] == f"/srv/keys/{identifier}"
    assert last_report(airgap_config)["actions"]["cleaned"] is None

    # Inserted again: the same identifier, the token is not written again
    token = (usb_tree / TOKEN_FILE).read_bytes()
    run_kiosk(airgap_config, RecordingDisplay(), usb_tree, pool)
    assert service.keys == [identifier, identifier]
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
    identifier = verify_token(read_token(usb_tree), token_key)
    assert identifier != foreign and service.keys == [identifier]


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
    assert last_report(config)["actions"]["airgap"]["status"] == "refused"


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
    assert "Copy to the server FAILED: sftp: [Errno 113] No route to host" in display.messages
    action = last_report(airgap_config)["actions"]["airgap"]
    assert action["status"] == "failed" and "No route to host" in action["error"]
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


def test_staged_type() -> None:
    assert Staged(Path("/x")).files == []
