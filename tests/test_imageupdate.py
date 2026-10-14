from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pandora_box import imageupdate, sigsets
from pandora_box.config import Config
from pandora_box.device import UsbDevice
from pandora_box.engines.fake import EICAR
from pandora_box.imageupdate import FILES, running_version, slot_partitions, stage
from pandora_box.kiosk import Kiosk, build_pool
from pandora_box.monitor import NO_DEVICE, Action, DeviceEvent

from .conftest import DirectoryMounter, ListSource, RecordingDisplay
from .test_sigsets import make_key

pytestmark = pytest.mark.skipif(not Path("/usr/bin/openssl").exists(), reason="no openssl")

UUIDS = {
    "root": "27bf1aef-352d-b2b9-41db-ff855174e48a",
    "verity": "0793ab9d-8b62-1158-c9c9-a3825a136f4f",
    "verity-sig": "56abaa07-2983-4fb3-a586-2cf3c0608d04",
}


@pytest.fixture
def key(tmp_path: Path) -> Path:
    return make_key(tmp_path / "keys", "update")


def make_update(
    folder: Path, version: int, key: Path, kinds: tuple[str, ...] = ("root", "verity", "verity-sig")
) -> Path:
    files = folder / FILES
    files.mkdir(parents=True)
    for kind in kinds:
        (files / f"pandora-box_{version}_{UUIDS[kind]}.{kind}.raw.xz").write_bytes(kind.encode())
    (files / f"pandora-box_{version}.efi").write_bytes(b"MZ uki")
    sigsets.build(folder, version, content=sigsets.CONTENT_IMAGE)
    sigsets.sign(folder, key)
    return folder


def test_stage(tmp_path: Path, key: Path) -> None:
    update = make_update(tmp_path / "update", 2, key)
    staging = tmp_path / "staging"
    manifest = stage(update, [key.with_suffix(".pem")], staging, running=1)
    assert manifest.serial == 2
    staged = sorted(p.name for p in (staging / FILES).iterdir())
    assert staged == sorted(p.name for p in (update / FILES).iterdir())
    # A second stage replaces the first one
    stage(update, [key.with_suffix(".pem")], staging, running=1)


@pytest.mark.parametrize("running", [2, 3])
def test_older_image_is_not_installed(tmp_path: Path, key: Path, running: int) -> None:
    update = make_update(tmp_path / "update", 2, key)
    with pytest.raises(sigsets.NotNewerError, match="not newer than the running"):
        stage(update, [key.with_suffix(".pem")], tmp_path / "staging", running=running)


def test_incomplete_update_is_refused(tmp_path: Path, key: Path) -> None:
    update = make_update(tmp_path / "update", 2, key, kinds=("root", "verity"))
    with pytest.raises(sigsets.SignatureSetError, match="incomplete image update"):
        stage(update, [key.with_suffix(".pem")], tmp_path / "staging", running=1)


def test_unexpected_file_is_refused(tmp_path: Path, key: Path) -> None:
    update = tmp_path / "update"
    (update / FILES).mkdir(parents=True)
    (update / FILES / "pandora-box_3.efi").write_bytes(b"another version")
    make_update(tmp_path / "unused", 2, key)
    for path in (tmp_path / "unused" / FILES).iterdir():
        path.rename(update / FILES / path.name)
    sigsets.build(update, 2, content=sigsets.CONTENT_IMAGE)
    sigsets.sign(update, key)
    with pytest.raises(sigsets.SignatureSetError, match=r"unexpected file.*pandora-box_3\.efi"):
        stage(update, [key.with_suffix(".pem")], tmp_path / "staging", running=1)


def test_contents_are_not_interchangeable(tmp_path: Path, key: Path) -> None:
    keys = [key.with_suffix(".pem")]
    update = make_update(tmp_path / "update", 2, key)
    with pytest.raises(sigsets.SignatureSetError, match="not a manifest of signatures"):
        sigsets.install(update, tmp_path / "signatures", keys)
    signature_set = tmp_path / "set"
    (signature_set / "clamav").mkdir(parents=True)
    (signature_set / "clamav/test.hdb").write_bytes(b"x")
    sigsets.build(signature_set, 5)
    sigsets.sign(signature_set, key)
    with pytest.raises(sigsets.SignatureSetError, match="not a manifest of image"):
        stage(signature_set, keys, tmp_path / "staging", running=1)


def test_running_version(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=debian\nIMAGE_ID=pandora-box\nIMAGE_VERSION="7"\n')
    assert running_version(os_release) == 7
    os_release.write_text("ID=debian\n")
    assert running_version(os_release) is None
    assert running_version(tmp_path / "missing") is None


@pytest.mark.skipif(not Path("/usr/sbin/sfdisk").exists(), reason="no sfdisk")
def test_slot_partitions(tmp_path: Path) -> None:
    image = tmp_path / "image.raw"
    image.write_bytes(bytes(8 * 1024 * 1024))
    table = "label: gpt\n" + "".join(
        f'size=1MiB, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, uuid={uuid}, name="{name}"\n'
        for name, uuid in (
            ("pandora-box_4", UUIDS["root"]),
            ("pandora-box_4_verity", UUIDS["verity"]),
            ("pandora-box_4_veritysig", UUIDS["verity-sig"]),
            ("_empty", "11111111-2222-3333-4444-555555555555"),
        )
    )
    subprocess.run(["/usr/sbin/sfdisk", "-q", str(image)], input=table, text=True, check=True)
    assert slot_partitions(image) == (
        4,
        {"": UUIDS["root"], "_verity": UUIDS["verity"], "_veritysig": UUIDS["verity-sig"]},
    )


def run_kiosk(
    config: Config, display: RecordingDisplay, root: Path, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(imageupdate, "STAGING", root.parent / "staging")
    monkeypatch.setattr(imageupdate, "running_version", lambda: 1)
    monkeypatch.setattr(
        imageupdate,
        "stage",
        lambda source, keys, running=None: stage(source, keys, root.parent / "staging", running),
    )
    monkeypatch.setattr(imageupdate, "apply", lambda: calls.append("apply"))
    monkeypatch.setattr(imageupdate, "reboot", lambda: calls.append("reboot"))
    pool = build_pool(config)
    pool.start()
    try:
        events = [DeviceEvent(Action.ADD, UsbDevice("/dev/sdb1", "vfat", "IMAGE"))]
        Kiosk(config, display, ListSource(events), pool, DirectoryMounter(root)).run()
    finally:
        pool.stop()
    return calls


def test_kiosk_installs_an_image_update_then_restarts(
    config: Config, display: RecordingDisplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = make_key(config.signatures.keys, "update")
    root = tmp_path / "device"
    make_update(root / imageupdate.UPDATE_FOLDER, 2, key)
    (root / "eicar.com").write_bytes(EICAR)
    calls = run_kiosk(config, display, root, monkeypatch)
    assert calls == ["apply", "reboot"]
    assert "System version 2 installed: the kiosk restarts." in display.messages
    assert "An image update device is not scanned. Remove the device." in display.messages
    assert (root / "eicar.com").exists()


def test_kiosk_refuses_an_image_signed_by_another_key(
    config: Config, display: RecordingDisplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_key(config.signatures.keys, "update")
    foreign = make_key(tmp_path / "foreign", "foreign")
    root = tmp_path / "device"
    make_update(root / imageupdate.UPDATE_FOLDER, 2, foreign)
    calls = run_kiosk(config, display, root, monkeypatch)
    assert calls == []
    assert any(m.startswith("Image update REFUSED: invalid signature") for m in display.messages)


def online_config(tmp_path: Path, url: str) -> Config:
    from pandora_box.config import parse_config

    return parse_config(
        {
            "kiosk": {"name": "test", "fake_scan": True},
            "scan": {"sandbox": False},
            "signatures": {"keys": str(tmp_path / "keys")},
            "updates": {"image_url": url},
        }
    )


def test_download_and_install_online(
    tmp_path: Path, key: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .test_online import Server

    server = Server(tmp_path / "www")
    try:
        make_update(tmp_path / "www" / "image", 2, key)
        config = online_config(tmp_path, server.url("image"))
        downloaded = tmp_path / "downloaded"
        manifest = imageupdate.download(config, downloaded, running=1, log=lambda _: None)
        assert manifest is not None
        assert manifest.serial == 2
        assert len([p for p in server.requests if "/files/" in p]) == 4
        # Up to date: nothing more than the manifest and its signature
        server.requests.clear()
        assert (
            imageupdate.download(config, tmp_path / "again", running=2, log=lambda _: None) is None
        )
        assert all(p.endswith(("manifest.json", ".sig")) for p in server.requests)
    finally:
        server.httpd.shutdown()

    applied: list[bool] = []
    flag = tmp_path / "run" / "restart"
    monkeypatch.setattr(imageupdate, "STAGING", tmp_path / "staging")
    monkeypatch.setattr(imageupdate, "RESTART_FLAG", flag)
    monkeypatch.setattr(imageupdate, "apply", lambda: applied.append(True))
    monkeypatch.setattr(
        imageupdate, "stage", lambda source, keys, running=None: stage(
            source, keys, tmp_path / "staging", running
        )
    )  # fmt: skip
    installed = imageupdate.install_staged(downloaded, [key.with_suffix(".pem")], running=1)
    assert installed is not None
    assert applied == [True]
    assert flag.read_text() == "2\n"
    assert list(downloaded.iterdir()) == []
    # Nothing staged any more
    assert imageupdate.install_staged(downloaded, [key.with_suffix(".pem")], running=1) is None


def test_idle_kiosk_restarts_after_an_online_image_update(
    config: Config, display: RecordingDisplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flag = tmp_path / "restart"
    flag.write_text("3\n")
    reboots: list[bool] = []
    monkeypatch.setattr(imageupdate, "RESTART_FLAG", flag)
    monkeypatch.setattr(imageupdate, "reboot", lambda: reboots.append(True))
    pool = build_pool(config)
    pool.start()
    try:
        events = [DeviceEvent(Action.IDLE, NO_DEVICE), DeviceEvent(Action.IDLE, NO_DEVICE)]
        Kiosk(config, display, ListSource(events), pool, DirectoryMounter(tmp_path)).run()
    finally:
        pool.stop()
    assert reboots == [True]
    assert "System version 3 installed: the kiosk restarts." in display.messages
