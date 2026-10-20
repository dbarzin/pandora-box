"""End-to-end test: a disk image attached to a loop device simulates a USB key.

Requires root, loop devices and mkfs tools: run it with tests/e2e/run.sh.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import jsonschema
import pytest

from pandora_box.bloom import write_filter
from pandora_box.config import parse_config
from pandora_box.device import UsbDevice
from pandora_box.engines import FileInfo, Verdict
from pandora_box.engines.clamav import ClamavEngine
from pandora_box.hashdb import write_database
from pandora_box.kiosk import Kiosk, build_pool, make_mounter
from pandora_box.monitor import Action, DeviceEvent
from pandora_box.report import load_schema

from ..conftest import ListSource, RecordingDisplay
from ..samples import eicar

EICAR = eicar()

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.geteuid() != 0, reason="needs root to use loop devices"),
]

MKFS = {
    "vfat": ["mkfs.vfat", "-n", "USBKEY"],
    "exfat": ["mkfs.exfat", "-L", "USBKEY"],
    "ext4": ["mkfs.ext4", "-q", "-L", "USBKEY"],
    # -F: a file, not a block device; -Q: quick format
    "ntfs": ["mkntfs", "-F", "-Q", "-L", "USBKEY"],
}
# Mount type that fills a key: NTFS with ntfs-3g, as root (the kernel of the
# host may have no NTFS driver)
POPULATE = {"ntfs": "ntfs-3g"}


def run(*argv: str) -> str:
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()


def attach(image: Path) -> str:
    """Attach the image to a free loop device, creating the node if missing.

    Containers have a static /dev: the node of a new loop device must be created.
    """
    # losetup prints "/dev/loopN (lost)" when the node does not exist
    node = run("losetup", "--find").split()[0]
    if not Path(node).exists():
        os.mknod(node, 0o660 | 0o060000, os.makedev(7, int(node.removeprefix("/dev/loop"))))
    run("losetup", node, str(image))
    # The kiosk leaves a scanned device read-only, the loop device keeps it
    run("blockdev", "--setrw", node)
    return node


def mount_options(node: str) -> list[str]:
    for line in Path("/proc/self/mounts").read_text().splitlines():
        fields = line.split()
        if fields[0] == node:
            return fields[3].split(",")
    return []


def mount_type(node: str) -> str:
    for line in Path("/proc/self/mounts").read_text().splitlines():
        fields = line.split()
        if fields[0] == node:
            return fields[2]
    return ""


# vfat stays in the kernel; the other filesystems are read by a FUSE driver
# in the sandbox (pandora_box.fuse)
MOUNT_TYPES = {"vfat": "vfat", "exfat": "fuse.exfat", "ntfs": "fuse.ntfs", "ext4": "fuse.ext4"}


@contextmanager
def make_key(tmp_path: Path, fs_type: str, files: dict[str, bytes]) -> Iterator[UsbDevice]:
    """A disk image with the given files, attached to a loop device."""
    if shutil.which(MKFS[fs_type][0]) is None:
        pytest.skip(f"{MKFS[fs_type][0]} not installed")
    image = tmp_path / f"{fs_type}.img"
    with image.open("wb") as f:
        f.truncate(64 * 1024 * 1024)
    run(*MKFS[fs_type], str(image))
    node = attach(image)
    try:
        populate = tmp_path / "populate"
        populate.mkdir()
        run("mount", "-t", POPULATE.get(fs_type, fs_type), node, str(populate))
        for name, content in files.items():
            (populate / name).parent.mkdir(parents=True, exist_ok=True)
            (populate / name).write_bytes(content)
        run("umount", str(populate))
        yield UsbDevice(node=node, fs_type=fs_type, label="USBKEY", model="Loop")
    finally:
        subprocess.run(["umount", str(tmp_path / "populate")], capture_output=True)
        subprocess.run(["losetup", "-d", node], capture_output=True)


def key_files(node: str, tmp_path: Path) -> list[str]:
    """Files left on the key, read from a fresh read-only mount."""
    check = tmp_path / "check"
    check.mkdir()
    run("mount", "-o", "ro", node, str(check))
    try:
        files = sorted(str(p.relative_to(check)) for p in check.rglob("*") if p.is_file())
    finally:
        run("umount", str(check))
    return [f for f in files if not f.startswith("lost+found")]


@pytest.fixture(params=sorted(MKFS))
def usb_key(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[UsbDevice]:
    files = {
        "readme.txt": b"hello",
        "docs/report.pdf": b"%PDF-1.4 harmless",
        "docs/eicar.com": EICAR,
        "eicar copy.txt": EICAR + b"\n",
    }
    with make_key(tmp_path, request.param, files) as key:
        yield key


def test_scan_and_clean(usb_key: UsbDevice, tmp_path: Path) -> None:
    mount_point = tmp_path / "media"
    mount_point.mkdir()
    config = parse_config(
        {
            "kiosk": {"name": "e2e", "fake_scan": True, "interface": "console"},
            # A loop device cannot be ejected
            "device": {"mount_point": str(mount_point), "eject": False},
            "quarantine": {"folder": str(tmp_path / "quarantine")},
            "report": {"folder": str(tmp_path / "reports")},
            "logging": {"file": str(tmp_path / "pandora-box.log")},
        }
    )
    display = RecordingDisplay()
    options_during_scan: list[list[str]] = []
    original_progress = display.progress

    types_during_scan: set[str] = set()

    def record(percent: int) -> None:
        options_during_scan.append(mount_options(usb_key.node))
        types_during_scan.add(mount_type(usb_key.node))
        original_progress(percent)

    display.progress = record  # type: ignore[method-assign]
    mounted_at_confirm: list[bool] = []
    original_confirm = display.confirm

    def confirm(prompt: str) -> None:
        mounted_at_confirm.append(os.path.ismount(mount_point))
        original_confirm(prompt)

    display.confirm = confirm  # type: ignore[method-assign]
    mounter = make_mounter(config)
    source = ListSource([DeviceEvent(Action.ADD, usb_key)])
    with build_pool(config) as pool:
        Kiosk(config, display, source, pool, mounter).run()

    # The device was scanned read-only with hardened options, then unmounted
    assert options_during_scan
    for options in options_during_scan:
        assert {"ro", "noexec", "nosuid", "nodev"} <= set(options)
    assert types_during_scan == {MOUNT_TYPES[usb_key.fs_type]}
    assert not os.path.ismount(mount_point)
    # The user could remove the device while asked to confirm the cleaning
    assert mounted_at_confirm == [False]
    assert "Device cleaned! You can remove the device." in display.messages

    # Infected files are quarantined
    manifests = list((tmp_path / "quarantine").glob("*/manifest.json"))
    assert len(manifests) == 1
    quarantined = {e["original_path"] for e in json.loads(manifests[0].read_text())["files"]}
    assert quarantined == {"docs/eicar.com", "eicar copy.txt"}

    # Infected files are removed from the key, clean files are kept
    assert key_files(usb_key.node, tmp_path) == ["docs/report.pdf", "readme.txt"]


@pytest.mark.parametrize("fs_type", ["ext4", "ntfs"])
def test_stopped_driver_is_not_verified(fs_type: str, tmp_path: Path) -> None:
    """The FUSE driver dies during the scan: the key is not verified."""
    mount_point = tmp_path / "media"
    mount_point.mkdir()
    config = parse_config(
        {
            "kiosk": {"name": "e2e", "fake_scan": True, "interface": "console"},
            "device": {"mount_point": str(mount_point), "eject": False},
            "quarantine": {"folder": str(tmp_path / "quarantine")},
            "report": {"folder": str(tmp_path / "reports")},
            "logging": {"file": str(tmp_path / "pandora-box.log")},
        }
    )
    files = {f"file{i}.txt": b"hello" for i in range(5)}
    display = RecordingDisplay()
    mounter = make_mounter(config)
    original_progress = display.progress

    def kill_driver(percent: int) -> None:
        # After the first file scanned
        if percent and mounter.driver is not None and mounter.driver.process.poll() is None:
            mounter.driver.process.kill()
            mounter.driver.process.wait()
        original_progress(percent)

    display.progress = kill_driver  # type: ignore[method-assign]
    with make_key(tmp_path, fs_type, files) as key, build_pool(config) as pool:
        Kiosk(config, display, ListSource([DeviceEvent(Action.ADD, key)]), pool, mounter).run()
        assert not os.path.ismount(mount_point)
    assert display.messages[-1] == "DEVICE NOT VERIFIED: do not use it. Remove the device."
    [report_path] = list((tmp_path / "reports").glob("*.json"))
    reasons = json.loads(report_path.read_text())["verdict"]["incomplete_reasons"]
    assert any("filesystem driver" in r and "stopped" in r for r in reasons), reasons


# -- Real engines -------------------------------------------------------------

YARA_RULE = """
rule PandoraBox_E2E_Marker {
    meta: score = 90
    strings: $a = "PANDORA-BOX-E2E-YARA-MARKER"
    condition: $a
}
"""
MALWAREBAZAAR_SAMPLE = b"pretend malware sample listed in MalwareBazaar\n"
KNOWN_FILE = b"a well known file listed in hashlookup\n"


@pytest.fixture
def clamd() -> Iterator[Path]:
    """A real clamd with a test-only database: it only detects EICAR."""
    executable = shutil.which("clamd") or "/usr/sbin/clamd"
    if not Path(executable).exists():
        pytest.skip("clamd not installed")
    # Short path: Unix socket paths are limited to 108 bytes
    folder = Path(tempfile.mkdtemp(prefix="clamd-"))
    # Like /run/clamav: the sandboxed scan workers connect to the socket
    folder.chmod(0o755)
    database = folder / "db"
    database.mkdir()
    (database / "pandora-box-test.hdb").write_text(
        f"{hashlib.md5(EICAR).hexdigest()}:{len(EICAR)}:PandoraBox.Test.EICAR\n"
    )
    socket_path = folder / "clamd.sock"
    config = folder / "clamd.conf"
    config.write_text(
        f"LocalSocket {socket_path}\n"
        "LocalSocketMode 666\n"
        f"DatabaseDirectory {database}\n"
        f"TemporaryDirectory {folder}\n"
        "Foreground yes\n"
        "AlertExceedsMax yes\n"
    )
    process = subprocess.Popen([executable, "--config-file", str(config)])
    try:
        deadline = time.monotonic() + 60
        while not socket_path.exists():
            assert process.poll() is None, "clamd exited"
            assert time.monotonic() < deadline, "clamd did not start"
            time.sleep(0.2)
        yield socket_path
    finally:
        process.terminate()
        process.wait(timeout=30)
        shutil.rmtree(folder, ignore_errors=True)


@pytest.mark.parametrize("fs_type", ["vfat", "ext4"])
def test_real_engines(fs_type: str, clamd: Path, tmp_path: Path) -> None:
    files = {
        "readme.txt": b"hello",
        "docs/eicar.com": EICAR,
        "docs/marker.bin": b"xx PANDORA-BOX-E2E-YARA-MARKER xx",
        "sample.bin": MALWAREBAZAAR_SAMPLE,
        "known.txt": KNOWN_FILE,
    }
    signatures = tmp_path / "signatures"
    signatures.mkdir()
    write_database([hashlib.sha256(MALWAREBAZAAR_SAMPLE).digest()], signatures / "mb.bin")
    write_filter(
        signatures / "known.bloom", [hashlib.sha1(KNOWN_FILE).hexdigest().upper().encode()]
    )
    (signatures / "rules.yar").write_text(YARA_RULE)
    mount_point = tmp_path / "media"
    mount_point.mkdir()
    config = parse_config(
        {
            "kiosk": {"name": "e2e", "interface": "console"},
            # A loop device cannot be ejected
            "device": {"mount_point": str(mount_point), "eject": False},
            "engines": {
                "malwarebazaar": {"database": str(signatures / "mb.bin")},
                # Opt-in: content engines skipped on known files
                "hashlookup": {
                    "bloom": str(signatures / "known.bloom"),
                    "skip_content_engines": True,
                },
                "clamav": {"socket": str(clamd)},
                "yara": {
                    "rules": [{"name": "e2e", "path": str(signatures / "rules.yar")}],
                    "cache_dir": str(tmp_path / "cache"),
                },
            },
            "quarantine": {"folder": str(tmp_path / "quarantine")},
            "report": {"folder": str(tmp_path / "reports")},
            "logging": {"file": str(tmp_path / "pandora-box.log")},
        }
    )
    display = RecordingDisplay()
    with make_key(tmp_path, fs_type, files) as key, build_pool(config) as pool:
        mounter = make_mounter(config)
        Kiosk(config, display, ListSource([DeviceEvent(Action.ADD, key)]), pool, mounter).run()
        remaining = key_files(key.node, tmp_path)

    assert "Device cleaned! You can remove the device." in display.messages
    assert remaining == ["known.txt", "readme.txt"]

    [report_path] = list((tmp_path / "reports").glob("*.json"))
    report = json.loads(report_path.read_text())
    jsonschema.validate(report, load_schema())
    assert [e["name"] for e in report["engines"]] == [
        "malwarebazaar",
        "hashlookup",
        "clamav",
        "yara",
        "heuristics",
    ]
    clamav = next(e for e in report["engines"] if e["name"] == "clamav")
    assert clamav["version"].startswith("ClamAV ")

    def detections(path: str) -> dict[str, list[str]]:
        entry = next(f for f in report["files"] if f["path"] == path)
        return {e["engine"]: e["detections"] for e in entry["engines"] if e["detections"]}

    assert detections("docs/eicar.com") == {"clamav": ["PandoraBox.Test.EICAR.UNOFFICIAL"]}
    eicar_entry = next(f for f in report["files"] if f["path"] == "docs/eicar.com")
    clamav_result = next(e for e in eicar_entry["engines"] if e["engine"] == "clamav")
    # The descriptor was passed to clamd (no fallback to INSTREAM)
    assert clamav_result["facts"] == {"mode": "fildes"}
    assert detections("docs/marker.bin") == {"yara": ["e2e:PandoraBox_E2E_Marker"]}
    assert detections("sample.bin") == {"malwarebazaar": ["MalwareBazaar.KnownMalware"]}
    known = next(f for f in report["files"] if f["path"] == "known.txt")
    assert known["verdict"] == "clean"
    assert known["detail"] == "known file (hashlookup)"
    assert {e["engine"]: e["verdict"] for e in known["engines"]} == {
        "malwarebazaar": "clean",
        "hashlookup": "clean",
        "clamav": "skipped",
        "yara": "skipped",
        "heuristics": "skipped",
    }
    assert report["verdict"]["device"] == "malicious"
    assert report["actions"]["cleaned"] is True


@pytest.mark.parametrize("mode", ["fildes", "instream"])
def test_real_clamd_modes(mode: str, clamd: Path, tmp_path: Path) -> None:
    engine = ClamavEngine(clamd, mode=mode)
    engine.load()
    for content, verdict in ((EICAR, Verdict.MALICIOUS), (b"harmless\n", Verdict.CLEAN)):
        sample = tmp_path / "sample"
        sample.write_bytes(content)
        fd = os.open(sample, os.O_RDONLY)
        try:
            info = FileInfo("sample", len(content), "", "", "", fd=fd)
            result = engine.scan(info)
        finally:
            os.close(fd)
        assert result.verdict is verdict, result
        assert result.facts["mode"] == mode


def test_key_with_several_partitions(tmp_path: Path) -> None:
    """Each filesystem mounted and scanned in turn, cleaned where infected.

    A container has a static /dev, without the nodes of the partitions of a
    loop device: each partition is a loop device of its own here. The
    detection of the partitions by udev is tested in the virtual machine.
    """
    mount_point = tmp_path / "media"
    mount_point.mkdir()
    config = parse_config(
        {
            "kiosk": {"name": "e2e", "fake_scan": True, "interface": "console"},
            # A loop device cannot be ejected
            "device": {"mount_point": str(mount_point), "eject": False},
            "quarantine": {"folder": str(tmp_path / "quarantine")},
            "report": {"folder": str(tmp_path / "reports")},
            "logging": {"file": str(tmp_path / "pandora-box.log")},
        }
    )
    (tmp_path / "p1").mkdir()
    (tmp_path / "p2").mkdir()
    display = RecordingDisplay()
    with (
        make_key(tmp_path / "p1", "vfat", {"boot/loader.conf": b"timeout 0"}) as first,
        make_key(
            tmp_path / "p2", "ext4", {"readme.txt": b"hello", "docs/eicar.com": EICAR}
        ) as second,
        build_pool(config) as pool,
    ):
        key = UsbDevice(node=first.node, fs_type="vfat,ext4", partitions=(first, second))
        Kiosk(config, display, ListSource([DeviceEvent(Action.ADD, key)]), pool,
              make_mounter(config)).run()  # fmt: skip
        assert not os.path.ismount(mount_point)
        assert key_files(first.node, tmp_path / "p1") == ["boot/loader.conf"]
        assert key_files(second.node, tmp_path / "p2") == ["readme.txt"]

    assert "Device cleaned! You can remove the device." in display.messages
    [report_path] = list((tmp_path / "reports").glob("*.json"))
    report = json.loads(report_path.read_text())
    jsonschema.validate(report, load_schema())
    assert [(p["filesystem"], p["scanned"]) for p in report["device"]["partitions"]] == [
        ("vfat", True),
        ("ext4", True),
    ]
    name = Path(second.node).name
    assert report["actions"]["removed"] == [f"{name}/docs/eicar.com"]
