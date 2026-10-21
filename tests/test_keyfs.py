"""Files written on a key by the outbound station: the limits of its filesystem."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pandora_box.keyfs import FAT_MAX_FILE, name_problem, plan, write_file


@pytest.mark.parametrize(
    ("fs_type", "path", "refused"),
    [
        ("vfat", "report.pdf", False),
        ("vfat", "what?.txt", True),
        ("exfat", "a:b.txt", True),
        ("ntfs", "dot.", True),
        ("vfat", "docs/NUL.txt", True),
        ("ext4", "what?.txt", False),
        ("ext4", "a" * 255, False),
        ("ext4", "a" * 256, True),
        ("vfat", "é" * 255, False),
        ("ext4", "é" * 200, True),
    ],
)
def test_names(fs_type: str, path: str, refused: bool) -> None:
    assert bool(name_problem(fs_type, path)) is refused


def test_plan_limits_and_space() -> None:
    files = [("Big.iso", FAT_MAX_FILE + 1), ("a.txt", 10), ("A.TXT", 10), ("b.txt", 50)]
    result = plan("vfat", files, free=40)
    assert result.accepted == [("a.txt", 10)]
    reasons = dict(result.rejected)
    assert "4 GiB" in reasons["Big.iso"]
    assert "case ignored" in reasons["A.TXT"]
    assert "not enough space" in reasons["b.txt"]
    # ext4 knows the case and the big files
    assert len(plan("ext4", files, free=FAT_MAX_FILE * 2).accepted) == 4


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_write_file(tmp_path: Path) -> None:
    root, source = tmp_path / "key", tmp_path / "source"
    root.mkdir()
    source.write_bytes(b"content")
    write_file(root, "docs/deep/file.txt", source, sha(b"content"), 1_700_000_000)
    target = root / "docs/deep/file.txt"
    assert target.read_bytes() == b"content" and target.stat().st_mtime == 1_700_000_000
    # An existing file of that name is replaced
    source.write_bytes(b"new")
    write_file(root, "docs/deep/file.txt", source, sha(b"new"), 1_700_000_100)
    assert target.read_bytes() == b"new"
    assert not list(root.rglob("*.pandora-box-tmp"))


def test_write_file_checks_the_content_and_the_key(tmp_path: Path) -> None:
    root, source = tmp_path / "key", tmp_path / "source"
    root.mkdir()
    source.write_bytes(b"swapped after the scan")
    with pytest.raises(OSError, match="changed since the scan"):
        write_file(root, "a.txt", source, sha(b"scanned"), 0)
    assert not (root / "a.txt").exists() and not list(root.iterdir())
    source.write_bytes(b"x")
    # A link of the key never leads out of it
    (root / "out").symlink_to(tmp_path)
    with pytest.raises(OSError, match="not a folder"):
        write_file(root, "out/a.txt", source, sha(b"x"), 0)
    (root / "folder").mkdir()
    with pytest.raises(OSError, match="folder of the key"):
        write_file(root, "folder", source, sha(b"x"), 0)
