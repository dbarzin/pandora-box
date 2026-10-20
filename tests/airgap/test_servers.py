"""Copy to real servers (airgap): rsync over SSH, SFTP and SMB.

Run in containers with tests/airgap/run.sh: an OpenSSH server (with rsync)
and a Samba server (SMB 3, encryption required). The runner sees the folders
of the servers (/check), to compare them with what was sent.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from pandora_box import airgap
from pandora_box.config import AirgapConfig
from pandora_box.remotes import REPORTS, Secrets, Server, TransferError, upload

pytestmark = pytest.mark.airgap

SECRETS = Path("/secrets")
# Where each server writes, and where the runner sees it. rrsync: an account
# whose key may only run rsync in /srv/keys/rrsync (docs/airgap.md)
SERVERS = {
    "rsync": (
        dict(protocol="rsync", host="sshd", user="kiosk", path="/srv/keys"),
        Path("/check/ssh"),
    ),
    "rrsync": (
        dict(protocol="rsync", host="sshd", user="restricted", path="."),
        Path("/check/ssh/rrsync"),
    ),
    "sftp": (
        dict(protocol="sftp", host="sshd", user="kiosk", path="/srv/keys"),
        Path("/check/ssh"),
    ),
    "smb": (
        dict(protocol="smb", host="samba", user="kiosk", path="keys", share="transfers"),
        Path("/check/smb/keys"),
    ),
}


@pytest.fixture
def credentials(tmp_path: Path) -> Path:
    """The credentials of the service: the SSH key (0600, as ssh wants it), the password."""
    folder = tmp_path / "credentials"
    folder.mkdir()
    key = folder / airgap.SSH_KEY_CREDENTIAL
    shutil.copyfile(SECRETS / "id_ed25519", key)
    key.chmod(0o600)
    shutil.copyfile(SECRETS / "smb-password", folder / airgap.PASSWORD_CREDENTIAL)
    return folder


def server(name: str, known_hosts: Path = SECRETS / "known_hosts") -> Server:
    options, _ = SERVERS[name]
    return Server(port=0, known_hosts=known_hosts, timeout=20, **options)


def secrets(credentials: Path) -> Secrets:
    return Secrets(
        ssh_key=credentials / airgap.SSH_KEY_CREDENTIAL,
        password=(credentials / airgap.PASSWORD_CREDENTIAL).read_text().strip(),
    )


def write(path: Path, content: bytes, mtime: int = 1_700_000_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (mtime, mtime))


def files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.relative_to(root).as_posix().startswith(REPORTS)
    }


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_mirror(name: str, credentials: Path, tmp_path: Path) -> None:
    identifier = str(uuid.uuid4())
    srv, (_, check) = server(name), SERVERS[name]
    remote = f"{srv.path}/{identifier}"
    local = tmp_path / "local"
    write(local / "readme.txt", b"hello")
    write(local / "docs/report.pdf", b"%PDF-1.4 harmless")
    write(local / "Photos/été 2026.jpg", os.urandom(3 * 1024 * 1024))
    (local / "empty").mkdir()
    report = tmp_path / "report.json"
    report.write_text('{"report_id": "first"}')

    result = upload(local, report, srv, secrets(credentials), remote, "first.json")
    assert sorted(result.copied) == ["Photos/été 2026.jpg", "docs/report.pdf", "readme.txt"]
    assert files(check / identifier) == files(local)
    assert (check / identifier / "empty").is_dir()
    assert (check / identifier / REPORTS / "first.json").read_text() == '{"report_id": "first"}'
    sent = (check / identifier / "readme.txt").stat().st_mtime
    assert abs(sent - 1_700_000_000) <= 2

    # The key changed: one file changed, one added, one removed
    write(local / "docs/report.pdf", b"%PDF-1.4 new version", mtime=1_700_000_500)
    write(local / "new.txt", b"new")
    (local / "readme.txt").unlink()
    (local / "empty").rmdir()
    report.write_text('{"report_id": "second"}')
    result = upload(local, report, srv, secrets(credentials), remote, "second.json")
    assert sorted(result.copied) == ["docs/report.pdf", "new.txt"]
    assert "readme.txt" in result.deleted and "empty" in result.deleted
    assert result.unchanged == 1
    assert files(check / identifier) == files(local)
    assert not (check / identifier / "empty").exists()
    # The reports of every scan are kept
    reports = sorted(p.name for p in (check / identifier / REPORTS).iterdir())
    assert reports == ["first.json", "second.json"]

    # Nothing changed: nothing is copied
    result = upload(local, report, srv, secrets(credentials), remote, "second.json")
    assert result.copied == [] and result.deleted == []


@pytest.mark.parametrize("protocol", ["rsync", "sftp"])
def test_unknown_host_key_is_refused(protocol: str, credentials: Path, tmp_path: Path) -> None:
    other = tmp_path / "known_hosts"
    other.write_text(
        "sshd ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"
    )
    local = tmp_path / "local"
    write(local / "a.txt", b"a")
    report = tmp_path / "report.json"
    report.write_text("{}")
    with pytest.raises(TransferError):
        upload(
            local, report, server(protocol, other), secrets(credentials), "/srv/keys/x", "r.json"
        )


def test_wrong_smb_password(tmp_path: Path) -> None:
    local = tmp_path / "local"
    write(local / "a.txt", b"a")
    report = tmp_path / "report.json"
    report.write_text("{}")
    with pytest.raises(TransferError):
        wrong = Secrets(password="wrong")  # noqa: S106  (a wrong password, on purpose)
        upload(local, report, server("smb"), wrong, "keys/x", "r.json")


@pytest.fixture
def service_folder() -> Iterator[Path]:
    # Short path: Unix socket paths are limited to 108 bytes
    folder = Path(tempfile.mkdtemp(prefix="airgap-"))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_service(
    name: str,
    credentials: Path,
    service_folder: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kiosk stages the clean files, the service copies them over its socket."""
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))
    options, check = SERVERS[name]
    config = AirgapConfig(
        enabled=True,
        known_hosts=SECRETS / "known_hosts",
        staging=service_folder / "staging",
        socket=service_folder / "airgap.sock",
        timeout=20,
        **options,  # type: ignore[arg-type]
    )
    threading.Thread(target=airgap.serve, args=(config,), daemon=True).start()
    identifier, report_id = str(uuid.uuid4()), str(uuid.uuid4())
    key = tmp_path / "key"
    write(key / "clean.txt", b"clean")
    staged = airgap.prepare(config, identifier, 100)
    airgap.stage_file(staged, key, "clean.txt", "clean.txt", hashlib.sha256(b"clean").hexdigest())
    (staged.folder / airgap.REPORT).write_text('{"report": 1}')
    for _ in range(100):
        if config.socket.exists():
            break
        threading.Event().wait(0.05)

    result = airgap.request(config, identifier, report_id)
    assert result.copied == 1 and result.errors == []
    assert result.destination == f"{options['path']}/{identifier}"
    assert (check / identifier / "clean.txt").read_bytes() == b"clean"
    assert (check / identifier / REPORTS / f"{report_id}.json").read_text() == '{"report": 1}'
