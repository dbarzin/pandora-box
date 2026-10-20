"""Mirror of a folder to a server (airgap): the comparison, on a local "server"."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pandora_box import remotes
from pandora_box.remotes import (
    REPORTS,
    Entry,
    Secrets,
    Server,
    SmbFS,
    TransferError,
    mirror,
    rsync_mirror,
    scan_local,
    upload,
)


class LocalFS:
    """A remote folder that is a local folder: what the SFTP and SMB classes do."""

    def __init__(self, base: Path, case_insensitive: bool = False) -> None:
        self.base = base
        self.case_insensitive = case_insensitive
        self.puts: list[str] = []

    def _p(self, path: str) -> Path:
        return self.base / path.lstrip("/")

    def scan(self, root: str) -> dict[str, Entry] | None:
        path = self._p(root)
        return scan_local(path) if path.is_dir() else None

    def makedirs(self, path: str) -> None:
        self._p(path).mkdir(parents=True, exist_ok=True)

    def put(self, local: Path, path: str, mtime: int, progress: Callable[[int], None]) -> None:
        target = self._p(path)
        tmp = target.with_name(f".{target.name}.tmp")
        shutil.copyfile(local, tmp)
        os.utime(tmp, (mtime, mtime))
        tmp.replace(target)
        progress(local.stat().st_size)
        self.puts.append(path)

    def remove(self, path: str) -> None:
        self._p(path).unlink()

    def rmdir(self, path: str) -> None:
        self._p(path).rmdir()

    def check_name(self, path: str) -> str:
        return SmbFS.check_name(self, path) if self.case_insensitive else ""  # type: ignore[arg-type]

    def close(self) -> None:
        pass


def write(path: Path, content: str, mtime: int = 1_700_000_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    os.utime(path, (mtime, mtime))


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_first_copy_then_sync(tmp_path: Path) -> None:
    local, server = tmp_path / "local", tmp_path / "server"
    write(local / "a.txt", "a")
    write(local / "docs/b.txt", "b")
    (local / "empty").mkdir()
    server.mkdir()
    fs = LocalFS(server)
    result = mirror(local, fs, "keys/k1")
    assert sorted(result.copied) == ["a.txt", "docs/b.txt"]
    assert tree(server / "keys/k1") == {"a.txt": "a", "docs/b.txt": "b"}
    assert (server / "keys/k1/empty").is_dir()

    # Unchanged files are not copied again; changed, new and removed ones are
    write(local / "docs/b.txt", "b2", mtime=1_700_000_100)
    write(local / "c.txt", "c")
    (local / "a.txt").unlink()
    (local / "empty").rmdir()
    fs.puts.clear()
    result = mirror(local, fs, "keys/k1")
    assert sorted(result.copied) == ["c.txt", "docs/b.txt"]
    assert sorted(result.deleted) == ["a.txt", "empty"]
    assert tree(server / "keys/k1") == {"c.txt": "c", "docs/b.txt": "b2"}
    result = mirror(local, fs, "keys/k1")
    assert result.copied == [] and result.deleted == [] and result.unchanged == 2


def test_reports_are_protected(tmp_path: Path) -> None:
    local, server = tmp_path / "local", tmp_path / "server"
    write(local / "a.txt", "a")
    write(server / "k1" / REPORTS / "old.json", "{}")
    write(server / "k1" / "stale.txt", "x")
    result = mirror(local, LocalFS(server), "k1", protect=(REPORTS,))
    assert result.deleted == ["stale.txt"]
    assert (server / "k1" / REPORTS / "old.json").exists()


def test_file_replaced_by_a_folder(tmp_path: Path) -> None:
    local, server = tmp_path / "local", tmp_path / "server"
    write(local / "x/inside.txt", "i")
    write(server / "k1/x", "was a file")
    write(server / "k1/y/deep/old.txt", "old")
    mirror(local, LocalFS(server), "k1")
    assert tree(server / "k1") == {"x/inside.txt": "i"}
    assert not (server / "k1/y").exists()


def test_names_an_smb_server_cannot_hold(tmp_path: Path) -> None:
    local, server = tmp_path / "local", tmp_path / "server"
    write(local / "Report.txt", "1")
    write(local / "report.txt", "2")
    write(local / "what?.txt", "3")
    write(local / "dot.", "4")
    result = mirror(local, LocalFS(server, case_insensitive=True), "k1")
    assert result.copied == ["Report.txt"]
    reasons = dict(result.errors)
    assert "case ignored" in reasons["report.txt"]
    assert "refuses" in reasons["what?.txt"]
    assert "dot" in reasons["dot."]


def test_progress(tmp_path: Path) -> None:
    local, server = tmp_path / "local", tmp_path / "server"
    write(local / "a.txt", "a" * 10)
    write(local / "b.txt", "b" * 30)
    seen: list[tuple[int, int]] = []
    mirror(local, LocalFS(server), "k1", progress=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (40, 40)


def test_upload_puts_the_report(tmp_path: Path) -> None:
    local, server = tmp_path / "local", tmp_path / "server"
    write(local / "a.txt", "a")
    report = tmp_path / "report.json"
    report.write_text('{"report_id": "r"}')
    fs = LocalFS(server)
    result = upload(
        local, report, Server("sftp", "h", 0, "u", "keys"), Secrets(), "keys/k1", "r.json",
        fs_factory=lambda server, secrets: fs,
    )  # fmt: skip
    assert result.copied == ["a.txt"]
    assert (server / "keys/k1" / REPORTS / "r.json").read_text() == '{"report_id": "r"}'


def test_ssh_needs_a_known_host(tmp_path: Path) -> None:
    server = Server("rsync", "h", 0, "u", "keys", known_hosts=tmp_path / "missing")
    with pytest.raises(TransferError, match="known_hosts"):
        rsync_mirror(tmp_path, server, Secrets(ssh_key=tmp_path / "k"), "keys/k1")
    known = tmp_path / "known_hosts"
    known.write_text("h ssh-ed25519 AAAA\n")
    with pytest.raises(TransferError, match="no SSH key"):
        remotes.ssh_command(Server("rsync", "h", 0, "u", "keys", known_hosts=known), Secrets())


def test_rsync_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    known = tmp_path / "known hosts"
    known.write_text("h ssh-ed25519 AAAA\n")
    local = tmp_path / "local"
    write(local / "a.txt", "a")
    write(local / "b.txt", "b")
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        calls.append(argv)
        out = "<f+++++++++ a.txt\n*deleting   old.txt\n.d..t...... ./\n"
        return type("Done", (), {"returncode": 0, "stdout": out, "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", run)
    server = Server("rsync", "files.example.org", 2222, "kiosk", "keys", known_hosts=known)
    result = rsync_mirror(local, server, Secrets(ssh_key=tmp_path / "id"), "keys/k1", (REPORTS,))
    assert result.copied == ["a.txt"] and result.deleted == ["old.txt"] and result.unchanged == 1
    argv = calls[0]
    assert "--delete" in argv and f"--exclude=/{REPORTS}" in argv and "--no-links" in argv
    assert argv[-2:] == [f"{local}/", "kiosk@files.example.org:keys/k1/"]
    ssh = argv[argv.index("-e") + 1]
    # The host key must be known: never trusted on first use
    assert "StrictHostKeyChecking=yes" in ssh and "BatchMode=yes" in ssh
    assert f"'UserKnownHostsFile={known}'" in ssh and "-p 2222" in ssh


def test_rsync_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    known = tmp_path / "known_hosts"
    known.write_text("h ssh-ed25519 AAAA\n")

    def run(argv: list[str], **kwargs: Any) -> Any:
        err = "ssh: connect to host h port 22: Connection refused\nrsync error: (code 255)\n"
        return type("Failed", (), {"returncode": 255, "stdout": "", "stderr": err})()

    monkeypatch.setattr(subprocess, "run", run)
    server = Server("rsync", "h", 0, "u", "keys", known_hosts=known)
    with pytest.raises(TransferError, match=r"rsync failed \(255\)"):
        rsync_mirror(tmp_path, server, Secrets(ssh_key=known), "keys/k1")
