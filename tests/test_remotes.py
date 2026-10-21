"""Mirror of a folder to a server (airgap): the comparison, on a local "server"."""

from __future__ import annotations

import calendar
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
    TransferError,
    mirror,
    rsync_mirror,
    scan_local,
    upload,
)


class LocalFS:
    """A remote folder that is a local folder: what the SFTP class does."""

    def __init__(self, base: Path) -> None:
        self.base = base
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

    def get(self, path: str, local: Path, mtime: int) -> None:
        shutil.copyfile(self._p(path), local)
        os.utime(local, (mtime, mtime))

    def remove(self, path: str) -> None:
        self._p(path).unlink()

    def rmdir(self, path: str) -> None:
        self._p(path).rmdir()

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


def test_windows_names(tmp_path: Path) -> None:
    """A Windows server refuses some names, and ignores the case."""
    local, server = tmp_path / "local", tmp_path / "server"
    for name in (
        "Report.txt",
        "report.txt",
        "what?.txt",
        "dot.",
        "CON.txt",
        "con1.txt",
        "a:b/c.txt",
    ):
        write(local / name, name)
    result = mirror(local, LocalFS(server), "k1", windows_names=True)
    assert sorted(result.copied) == ["Report.txt", "con1.txt"]
    reasons = dict(result.errors)
    assert "case ignored" in reasons["report.txt"]
    assert "refuses" in reasons["what?.txt"]
    assert "dot" in reasons["dot."]
    assert "device" in reasons["CON.txt"]
    assert "a:b/c.txt" in reasons
    # Without the option, every name is copied
    assert len(mirror(local, LocalFS(tmp_path / "other"), "k1").copied) == 7


def test_sftp_rename_without_posix_rename(tmp_path: Path) -> None:
    """A server without the posix-rename extension: the target is replaced."""
    calls: list[tuple[str, ...]] = []

    class Sftp:
        def putfo(self, source: Any, path: str, callback: Any) -> None:
            calls.append(("put", path))

        def utime(self, path: str, times: Any) -> None:
            calls.append(("utime", path))

        def posix_rename(self, old: str, new: str) -> None:
            raise OSError("Operation unsupported")

        def remove(self, path: str) -> None:
            calls.append(("remove", path))

        def rename(self, old: str, new: str) -> None:
            calls.append(("rename", old, new))

    fs = remotes.SftpFS.__new__(remotes.SftpFS)
    fs._sftp = Sftp()
    local = tmp_path / "a.txt"
    write(local, "a")
    fs.put(local, "k1/a.txt", 1, lambda sent: None)
    assert calls[-2:] == [
        ("remove", "k1/a.txt"),
        ("rename", "k1/.a.txt.pandora-box-tmp", "k1/a.txt"),
    ]


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


def test_fetch_the_files_the_key_does_not_have(tmp_path: Path) -> None:
    server = tmp_path / "server"
    outbound = server / "keys/k1" / remotes.OUTBOUND
    write(outbound / "same.txt", "same")
    write(outbound / "changed.txt", "new content", mtime=1_700_000_500)
    write(outbound / "docs/new.txt", "new")
    local = tmp_path / "downloads"
    local.mkdir()
    have = {"same.txt": Entry(4, 1_700_000_000), "changed.txt": Entry(3, 1_700_000_000)}
    result = remotes.fetch(
        Server("sftp", "h", 0, "u", "keys"), Secrets(), "keys/k1", local, have,
        fs_factory=lambda _server, _secrets: LocalFS(server),
    )  # fmt: skip
    assert sorted(result.fetched) == ["changed.txt", "docs/new.txt"]
    assert result.unchanged == 1
    assert (local / "docs/new.txt").read_text() == "new"
    assert (local / "changed.txt").stat().st_mtime == 1_700_000_500


def test_fetch_without_outbound_folder(tmp_path: Path) -> None:
    local = tmp_path / "downloads"
    local.mkdir()
    result = remotes.fetch(
        Server("sftp", "h", 0, "u", "keys"), Secrets(), "keys/k1", local, {},
        fs_factory=lambda server, secrets: LocalFS(tmp_path / "server"),
    )  # fmt: skip
    assert result.fetched == [] and result.unchanged == 0


def test_rsync_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    known = tmp_path / "known_hosts"
    known.write_text("h ssh-ed25519 AAAA\n")
    output = (
        "drwxr-xr-x           4096 2026/10/21 06:30:22 .\n"
        "-rw-r--r--             12 2023/11/14 22:13:20 a file.txt\n"
        "drwxr-xr-x           4096 2026/10/21 06:30:22 docs\n"
        "-rw-r--r--        1234567 2026/10/21 06:30:22 docs/été.pdf\n"
    )
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        calls.append(argv)
        assert kwargs["env"]["TZ"] == "UTC"
        return type("Done", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", run)
    server = Server("rsync", "h", 0, "u", "keys", known_hosts=known)
    entries = remotes.rsync_list(server, Secrets(ssh_key=known), "keys/k1/.pandora-box/outbound")
    assert entries == {
        "a file.txt": Entry(12, 1_700_000_000),
        "docs": Entry(0, 0, True),
        "docs/été.pdf": Entry(1234567, calendar.timegm((2026, 10, 21, 6, 30, 22))),
    }
    assert "--no-human-readable" in calls[0] and "--list-only" in calls[0]


def test_rsync_list_of_a_missing_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    known = tmp_path / "known_hosts"
    known.write_text("h ssh-ed25519 AAAA\n")

    def run(argv: list[str], **kwargs: Any) -> Any:
        err = 'rsync: change_dir "/keys/k1/x" failed: No such file or directory (2)\n'
        return type("Failed", (), {"returncode": 23, "stdout": "", "stderr": err})()

    monkeypatch.setattr(subprocess, "run", run)
    server = Server("rsync", "h", 0, "u", "keys", known_hosts=known)
    assert remotes.rsync_list(server, Secrets(ssh_key=known), "keys/k1/x") == {}


@pytest.mark.parametrize("path", ["", "/etc/passwd", "../x", "a/../b", "a//b", "a/./b"])
def test_invalid_paths_of_the_server(path: str) -> None:
    assert remotes.relative_path(path)
