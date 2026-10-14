from __future__ import annotations

import os
from pathlib import Path

import pytest

from pandora_box.engines import FakeEngine, Verdict
from pandora_box.filetype import FileTypeDetector
from pandora_box.hashing import hash_fd
from pandora_box.inventory import (
    SPECIAL_FILE,
    SYMLINK,
    TOO_BIG,
    TOO_DEEP,
    UNREADABLE,
    Entry,
    Inventory,
    UnsafeFileError,
    open_entry,
    take_inventory,
)
from pandora_box.pipeline import scan_entry


def inventory(
    root: Path, max_files: int = 100, max_depth: int = 10, max_size: int = 1000
) -> Inventory:
    return take_inventory(root, max_files, max_depth, max_size)


def test_regular_files(usb_tree: Path) -> None:
    inv = inventory(usb_tree)
    assert sorted(e.rel_path for e in inv.files) == [
        "docs/eicar.com",
        "docs/report.pdf",
        "readme.txt",
    ]
    assert inv.complete
    assert inv.total_bytes == sum(e.size for e in inv.files)


def test_links_are_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside / "secret").mkdir(parents=True)
    (outside / "secret" / "file").write_text("secret")
    root = tmp_path / "media"
    root.mkdir()
    (root / "to-file").symlink_to(outside / "secret" / "file")
    (root / "to-dir").symlink_to(outside / "secret")
    (root / "loop").symlink_to(root)
    inv = inventory(root)
    assert inv.files == []
    assert {s.rel_path: s.reason for s in inv.skipped} == {
        "to-file": SYMLINK,
        "to-dir": SYMLINK,
        "loop": SYMLINK,
    }
    # Links are listed, they do not make the scan incomplete
    assert inv.complete


def test_special_files(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "fifo")
    inv = inventory(tmp_path)
    assert [(s.rel_path, s.reason, s.incomplete) for s in inv.skipped] == [
        ("fifo", SPECIAL_FILE, False)
    ]


def test_max_file_size(tmp_path: Path) -> None:
    (tmp_path / "big").write_bytes(b"x" * 11)
    (tmp_path / "small").write_bytes(b"x" * 10)
    inv = inventory(tmp_path, max_size=10)
    assert [e.rel_path for e in inv.files] == ["small"]
    assert [(s.rel_path, s.reason, s.incomplete) for s in inv.skipped] == [("big", TOO_BIG, True)]


def test_max_depth(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "file").write_text("x")
    (tmp_path / "a" / "ok").write_text("x")
    inv = inventory(tmp_path, max_depth=2)
    assert [e.rel_path for e in inv.files] == ["a/ok"]
    assert [(s.rel_path, s.reason) for s in inv.skipped] == [("a/b/c", TOO_DEEP)]
    assert not inv.complete
    assert "limits.max_depth" in inv.incomplete_reasons[0]


def test_max_files(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}").write_text("x")
    inv = inventory(tmp_path, max_files=5)
    assert len(inv.files) == 5
    assert inv.truncated
    assert "limits.max_files" in inv.incomplete_reasons[0]


def test_max_files_counts_folders(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"d{i}").mkdir()
    assert inventory(tmp_path, max_files=5).truncated


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_unreadable_folder(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "file").write_text("x")
    locked.chmod(0)
    try:
        inv = inventory(tmp_path)
    finally:
        locked.chmod(0o700)
    assert inv.skipped[0].rel_path == "locked"
    assert inv.skipped[0].reason.startswith(UNREADABLE)
    assert not inv.complete


def test_hostile_names(tmp_path: Path) -> None:
    names = [
        os.fsdecode(b"latin1-\xe9.txt"),  # not UTF-8
        "esc\x1b[2J\x07bell\nnewline.txt",
        "rtl-‮gnp.exe",
        "x" * 255,
    ]
    for name in names:
        (tmp_path / name).write_text("x")
    # A path longer than PATH_MAX is reached component by component
    dir_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    for _ in range(20):
        os.mkdir("d" * 250, dir_fd=dir_fd)
        next_fd = os.open("d" * 250, os.O_RDONLY | os.O_DIRECTORY, dir_fd=dir_fd)
        os.close(dir_fd)
        dir_fd = next_fd
    fd = os.open("deep.txt", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=dir_fd)
    os.write(fd, b"deep")
    os.close(fd)
    os.close(dir_fd)

    inv = inventory(tmp_path, max_depth=30)
    found = {e.rel_path for e in inv.files}
    assert set(names) <= found
    deep = next(e for e in inv.files if e.rel_path.endswith("deep.txt"))
    assert len(os.fsencode(deep.rel_path)) > 4096
    detector = FileTypeDetector()
    for entry in inv.files:
        result = scan_entry(tmp_path, entry, [FakeEngine()], detector)
        assert result.verdict is Verdict.CLEAN, entry.rel_path


def entry_for(root: Path, name: str) -> Entry:
    return next(e for e in inventory(root).files if e.rel_path == name)


def test_open_entry(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("hello")
    with open_entry(tmp_path, entry_for(tmp_path, "file")) as fd:
        assert hash_fd(fd).size == 5


def test_open_refuses_replaced_file(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("hello")
    entry = entry_for(tmp_path, "file")
    # Keep the old inode alive so that the new file cannot reuse it
    (tmp_path / "file").rename(tmp_path / "old")
    (tmp_path / "file").write_text("other")
    with pytest.raises(UnsafeFileError, match="replaced"), open_entry(tmp_path, entry):
        pass


def test_open_refuses_symlink_swap(tmp_path: Path) -> None:
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "file").write_text("hello")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "file").write_text("hello")
    entry = entry_for(tmp_path, "dir/file")
    # The folder is replaced by a link after the inventory
    (tmp_path / "dir" / "file").unlink()
    (tmp_path / "dir").rmdir()
    (tmp_path / "dir").symlink_to(tmp_path / "outside")
    with pytest.raises(OSError), open_entry(tmp_path, entry):
        pass


def test_open_refuses_changed_size(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("hello")
    entry = entry_for(tmp_path, "file")
    (tmp_path / "file").write_text("hello world")
    with pytest.raises(UnsafeFileError, match="size"), open_entry(tmp_path, entry):
        pass


@pytest.mark.parametrize("rel_path", ["../etc/passwd", "/etc/passwd", "a//b", "./a"])
def test_open_refuses_invalid_paths(tmp_path: Path, rel_path: str) -> None:
    with (
        pytest.raises(UnsafeFileError, match="invalid path"),
        open_entry(tmp_path, Entry(rel_path, 0, 0, 0)),
    ):
        pass


def test_scan_entry_error_on_swap(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("hello")
    entry = entry_for(tmp_path, "file")
    (tmp_path / "file").unlink()
    os.mkfifo(tmp_path / "file")
    result = scan_entry(tmp_path, entry, [FakeEngine()], FileTypeDetector())
    assert result.verdict is Verdict.ERROR
    assert result.unscanned
    assert "not a regular file" in result.detail
