"""Copy to a Windows server (airgap): Windows Server in a QEMU virtual machine.

Run in the build container by tests/windows/windows.sh:

    prepare ISO   install Windows unattended (autounattend.xml, setup.ps1)
                  into tests/windows/work/windows.qcow2: its OpenSSH server
                  set up as docs/airgap.md describes it, then shut down
    test          boot that disk (changes thrown away), copy keys to it over
                  SFTP with windows_names, check what the server holds

The ISO is not part of the repository: Windows Server 2025 evaluation
(180 days, no product key). The machine has a SATA disk and an e1000e
network adapter, which Windows drives without virtio drivers.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from pandora_box.remotes import REPORTS, Secrets, Server, TransferError, upload

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"
DISK = WORK / "windows.qcow2"
CLIENT_KEY = WORK / "kiosk"
OVMF_CODE = Path("/usr/share/OVMF/OVMF_CODE_4M.fd")
OVMF_VARS = Path("/usr/share/OVMF/OVMF_VARS_4M.fd")
SSH_PORT = 2222
# Image of the ISO: on the evaluation ISO of Windows Server 2025, 1 is
# Standard (Server Core), 2 Standard with the desktop
DEFAULT_INDEX = 1
INSTALL_TIMEOUT = 2 * 3600
BOOT_TIMEOUT = 900


class TestFailure(Exception):
    pass


def step(message: str) -> None:
    print(f"--- {message}", flush=True)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise TestFailure(message)


def qemu(disk: Path, vars_file: Path, extra: list[str]) -> list[str]:
    kvm = os.access("/dev/kvm", os.W_OK)
    return [
        "qemu-system-x86_64",
        "-machine", f"q35,accel={'kvm' if kvm else 'tcg'}",
        "-cpu", "host" if kvm else "max",
        "-smp", "2",
        "-m", "4096",
        "-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
        "-drive", f"if=pflash,format=raw,file={vars_file}",
        "-device", "ahci,id=ahci",
        "-drive", f"file={disk},if=none,id=disk,format=qcow2",
        "-device", "ide-hd,drive=disk,bus=ahci.0",
        "-device", "qemu-xhci",
        # Watch the machine: VNC on port 5900 of the container
        "-display", "none", "-vnc", ":0",
        *extra,
    ]  # fmt: skip


def answer_image(path: Path, index: int, password: str) -> None:
    """The answer files on a small FAT disk labelled PANDORA (a USB key for Setup)."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        xml = (HERE / "autounattend.xml").read_text()
        xml = xml.replace("@INDEX@", str(index)).replace("@PASSWORD@", password)
        (folder / "autounattend.xml").write_text(xml)
        shutil.copy(HERE / "setup.ps1", folder / "setup.ps1")
        shutil.copy(CLIENT_KEY.with_suffix(".pub"), folder / "kiosk.pub")
        with path.open("wb") as image:
            image.truncate(32 * 1024 * 1024)
        subprocess.run(["mkfs.vfat", "-n", "PANDORA", str(path)], check=True, capture_output=True)
        entries = [str(p) for p in sorted(folder.iterdir())]
        subprocess.run(["mcopy", "-i", str(path), *entries, "::"], check=True)


def prepare(iso: Path, index: int) -> int:
    step(f"install Windows from {iso.name} (image {index}): about 20 to 40 minutes")
    WORK.mkdir(exist_ok=True)
    check(CLIENT_KEY.exists(), f"no {CLIENT_KEY}: made by tests/windows/windows.sh")
    password = "Pb-" + secrets.token_urlsafe(12) + "9a!"
    (WORK / "administrator-password").write_text(password + "\n")
    answers = WORK / "answers.img"
    answer_image(answers, index, password)
    DISK.unlink(missing_ok=True)
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(DISK), "40G"], check=True)
    vars_file = WORK / "ovmf-vars.fd"
    shutil.copy(OVMF_VARS, vars_file)
    monitor = WORK / "monitor.sock"
    monitor.unlink(missing_ok=True)
    argv = qemu(DISK, vars_file, [
        "-drive", f"file={iso},if=none,id=cd,media=cdrom,readonly=on",
        "-device", "ide-cd,drive=cd,bus=ahci.1,bootindex=1",
        "-drive", f"file={answers},if=none,id=answers,format=raw",
        "-device", "usb-storage,drive=answers,removable=on",
        "-netdev", "user,id=net", "-device", "e1000e,netdev=net",
        "-monitor", f"unix:{monitor},server,nowait",
    ])  # fmt: skip
    process = subprocess.Popen(argv)
    # "Press any key to boot from CD or DVD": answered for the first minute
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and process.poll() is None:
        with contextlib.suppress(OSError), socket.socket(socket.AF_UNIX) as sock:
            sock.connect(str(monitor))
            sock.sendall(b"sendkey ret\n")
        time.sleep(1)
    try:
        status = process.wait(INSTALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        print("FAILED: Windows not installed in time (watch it over VNC on port 5900)")
        return 1
    if status != 0:
        print(f"FAILED: QEMU exited with status {status}")
        return 1
    print(f"Windows installed, OpenSSH set up: {DISK}")
    return 0


@contextmanager
def machine() -> Iterator[Path]:
    """Boot the prepared Windows (snapshot: changes thrown away); known_hosts of its SSH."""
    check(DISK.exists(), f"no {DISK}: run tests/windows/windows.sh prepare ISO first")
    with tempfile.TemporaryDirectory() as tmp:
        vars_file = Path(tmp) / "ovmf-vars.fd"
        shutil.copy(WORK / "ovmf-vars.fd", vars_file)
        argv = qemu(DISK, vars_file, [
            "-snapshot",
            "-netdev", f"user,id=net,hostfwd=tcp:127.0.0.1:{SSH_PORT}-:22",
            "-device", "e1000e,netdev=net",
        ])  # fmt: skip
        process = subprocess.Popen(argv)
        try:
            known_hosts = Path(tmp) / "known_hosts"
            deadline = time.monotonic() + BOOT_TIMEOUT
            while True:
                scan = subprocess.run(
                    ["ssh-keyscan", "-t", "ed25519", "-p", str(SSH_PORT), "127.0.0.1"],
                    capture_output=True, text=True, check=False,
                )  # fmt: skip
                if scan.stdout.strip():
                    known_hosts.write_text(scan.stdout)
                    break
                check(time.monotonic() < deadline, "the SSH server of Windows does not answer")
                check(process.poll() is None, "QEMU stopped")
                time.sleep(5)
            yield known_hosts
        finally:
            process.terminate()
            process.wait(30)


def write(path: Path, content: bytes, mtime: int = 1_700_000_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (mtime, mtime))


def remote_files(server: Server, known_hosts: Path, root: str) -> dict[str, tuple[bytes, int]]:
    """The files below root on the server, with their content and modification time."""
    client = paramiko.SSHClient()
    client.load_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(
        "127.0.0.1", port=SSH_PORT, username="kiosk", key_filename=str(CLIENT_KEY),
        look_for_keys=False, allow_agent=False, timeout=30,
    )  # fmt: skip
    files: dict[str, tuple[bytes, int]] = {}
    try:
        sftp = client.open_sftp()
        pending = [""]
        while pending:
            relative = pending.pop()
            for attr in sftp.listdir_attr(f"{root}/{relative}".rstrip("/")):
                path = f"{relative}/{attr.filename}".lstrip("/")
                if stat.S_ISDIR(attr.st_mode or 0):
                    pending.append(path)
                else:
                    with sftp.open(f"{root}/{path}", "rb") as f:
                        files[path] = (f.read(), int(attr.st_mtime or 0))
    finally:
        client.close()
    return files


def test() -> int:
    with tempfile.TemporaryDirectory() as tmp, machine() as known_hosts:
        work = Path(tmp)
        key = work / "kiosk"
        shutil.copy(CLIENT_KEY, key)
        key.chmod(0o600)
        server = Server(
            "sftp", "127.0.0.1", SSH_PORT, "kiosk", "/keys",
            known_hosts=known_hosts, timeout=60, windows_names=True,
        )  # fmt: skip
        credentials = Secrets(ssh_key=key)
        identifier = str(uuid.uuid4())
        remote = f"/keys/{identifier}"
        local = work / "local"
        report = work / "report.json"

        step("first copy: the names Windows refuses or confuses are reported")
        write(local / "readme.txt", b"hello")
        write(local / "Documents/report.pdf", b"%PDF-1.4 harmless")
        write(local / "Photos/été 2026.jpg", os.urandom(2 * 1024 * 1024))
        write(local / "Rapport.txt", b"upper")
        write(local / "rapport.txt", b"lower")
        write(local / "what?.txt", b"?")
        write(local / "CON.txt", b"device")
        report.write_text('{"report_id": "first"}')
        try:
            result = upload(local, report, server, credentials, remote, "first.json")
        except TransferError as ex:
            raise TestFailure(f"copy refused: {ex}") from ex
        expected = ["Documents/report.pdf", "Photos/été 2026.jpg", "Rapport.txt", "readme.txt"]
        check(sorted(result.copied) == expected, f"copied: {result.copied}")
        reasons = dict(result.errors)
        check(set(reasons) == {"rapport.txt", "what?.txt", "CON.txt"}, f"refused: {reasons}")
        files = remote_files(server, known_hosts, remote)
        check(
            sorted(files) == sorted([*expected, f"{REPORTS}/first.json"]),
            f"on the server: {sorted(files)}",
        )
        check(files["readme.txt"][0] == b"hello", "readme.txt: content")
        sent = files["readme.txt"][1]
        check(abs(sent - 1_700_000_000) <= 2, f"modification time: {sent}")
        print(f"{len(result.copied)} files copied, {len(reasons)} names refused, dates kept")

        step("synchronization: a file replaced, one added, one removed")
        write(local / "Documents/report.pdf", b"%PDF-1.4 new version", mtime=1_700_000_500)
        write(local / "new.txt", b"new")
        (local / "readme.txt").unlink()
        report.write_text('{"report_id": "second"}')
        result = upload(local, report, server, credentials, remote, "second.json")
        check(sorted(result.copied) == ["Documents/report.pdf", "new.txt"], f"copied: {result}")
        check(result.deleted == ["readme.txt"], f"deleted: {result.deleted}")
        files = remote_files(server, known_hosts, remote)
        check(files["Documents/report.pdf"][0] == b"%PDF-1.4 new version", "file not replaced")
        check("readme.txt" not in files, "readme.txt left on the server")
        check(f"{REPORTS}/first.json" in files, "first report deleted")
        print("replaced over the old file, removed file deleted, reports kept")

        step("nothing changed: nothing copied")
        result = upload(local, report, server, credentials, remote, "second.json")
        check(result.copied == [] and result.deleted == [], f"changes: {result}")
        print(f"{result.unchanged} files unchanged")

        step("a server with another host key is refused")
        other = work / "other_known_hosts"
        other.write_text(
            f"[127.0.0.1]:{SSH_PORT} ssh-ed25519 "
            "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"
        )
        foreign = Server("sftp", "127.0.0.1", SSH_PORT, "kiosk", "/keys", known_hosts=other)
        try:
            upload(local, report, foreign, credentials, remote, "x.json")
        except TransferError:
            print("refused")
        else:
            raise TestFailure("copied to a server with an unknown host key")
    print("PASSED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy to a Windows server in QEMU (airgap)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_prepare = sub.add_parser("prepare", help="install Windows into the test disk")
    p_prepare.add_argument("iso", type=Path)
    p_prepare.add_argument("--index", type=int, default=DEFAULT_INDEX, help="image of the ISO")
    sub.add_parser("test", help="copy keys to the prepared Windows")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            return prepare(args.iso, args.index)
        return test()
    except TestFailure as ex:
        print(f"FAILED: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
