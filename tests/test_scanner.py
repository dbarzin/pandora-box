from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import pytest

from pandora_box.config import LimitsConfig
from pandora_box.engines import EngineSpec, Verdict
from pandora_box.scanner import FileResult, Scanner
from pandora_box.workers import WorkerPool

from .conftest import started_pool
from .engines import MisbehavingEngine

LIMITS = LimitsConfig(max_file_size=1000, max_files=1000, max_depth=10)


def test_scan_tree(usb_tree: Path, fake_pool: WorkerPool) -> None:
    (usb_tree / "big.iso").write_bytes(b"0" * 2000)
    (usb_tree / "link").symlink_to("/etc/passwd")
    os.mkfifo(usb_tree / "fifo")
    progress: list[tuple[int, int]] = []

    def on_progress(result: FileResult, done: int, total: int) -> None:
        progress.append((done, total))

    summary = Scanner(fake_pool, LIMITS).scan_tree(usb_tree, on_progress)

    verdicts = {f.rel_path: f.verdict for f in summary.files}
    assert verdicts == {
        "readme.txt": Verdict.CLEAN,
        "docs/report.pdf": Verdict.CLEAN,
        "docs/eicar.com": Verdict.MALICIOUS,
        "big.iso": Verdict.SKIPPED,
        "link": Verdict.SKIPPED,
        "fifo": Verdict.SKIPPED,
    }
    assert [f.path.name for f in summary.infected] == ["eicar.com"]
    assert progress == [(i, 6) for i in range(1, 7)]
    # The big file was not scanned: the device was not fully scanned
    assert [f.rel_path for f in summary.unscanned] == ["big.iso"]
    assert not summary.complete


def test_complete_scan(usb_tree: Path, fake_pool: WorkerPool) -> None:
    summary = Scanner(fake_pool, LIMITS).scan_tree(usb_tree)
    assert summary.complete
    assert summary.unscanned == []


def test_engine_failure_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("x")
    with started_pool([EngineSpec("m", MisbehavingEngine, ("raise",))], 1) as pool:
        summary = Scanner(pool, LIMITS).scan_tree(tmp_path)
    assert summary.files[0].verdict is Verdict.ERROR
    assert summary.files[0].results[0].error == "RuntimeError: boom"
    assert not summary.complete


def test_many_files(tmp_path: Path, fake_pool: WorkerPool) -> None:
    for i in range(50):
        (tmp_path / f"f{i}").write_text(str(i))
    summary = Scanner(fake_pool, LIMITS).scan_tree(tmp_path)
    assert summary.count(Verdict.CLEAN) == 50


def test_file_info(usb_tree: Path, fake_pool: WorkerPool) -> None:
    summary = Scanner(fake_pool, LIMITS).scan_tree(usb_tree)
    infos = {f.rel_path: f.info for f in summary.files}
    readme = infos["readme.txt"]
    assert readme is not None
    assert readme.sha256 == hashlib.sha256(b"hello").hexdigest()
    assert readme.sha1 == hashlib.sha1(b"hello").hexdigest()
    assert readme.md5 == hashlib.md5(b"hello").hexdigest()
    assert readme.mime == "text/plain"
    assert readme.description is not None and "text" in readme.description
    report = infos["docs/report.pdf"]
    assert report is not None and report.mime == "application/pdf"


def test_missing_root(tmp_path: Path, fake_pool: WorkerPool) -> None:
    summary = Scanner(fake_pool, LIMITS).scan_tree(tmp_path / "missing")
    assert summary.files == []
    assert not summary.complete
    assert "cannot read the device" in summary.incomplete_reasons[0]


def test_engine_durations_are_logged(
    usb_tree: Path, fake_pool: WorkerPool, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        Scanner(fake_pool, LIMITS).scan_tree(usb_tree)
    scanned = [r.fields for r in caplog.records if r.getMessage() == "file_scanned"]  # type: ignore[attr-defined]
    assert scanned
    for fields in scanned:
        for verdict, _, duration in fields["engines"].values():
            assert verdict in ("clean", "malicious")
            assert isinstance(duration, float) and duration >= 0
