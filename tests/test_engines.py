from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from pandora_box.engines import EngineResult, EngineSpec, FakeEngine, FileInfo, Verdict, aggregate
from pandora_box.engines.fake import EICAR


@contextmanager
def file_info(path: Path) -> Iterator[FileInfo]:
    fd = os.open(path, os.O_RDONLY)
    try:
        yield FileInfo(path.name, path.stat().st_size, "", "", "", fd=fd)
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ([], Verdict.ERROR),
        ([Verdict.CLEAN], Verdict.CLEAN),
        ([Verdict.CLEAN, Verdict.MALICIOUS], Verdict.MALICIOUS),
        ([Verdict.SUSPICIOUS, Verdict.ERROR], Verdict.SUSPICIOUS),
        ([Verdict.CLEAN, Verdict.ERROR], Verdict.ERROR),
        ([Verdict.SKIPPED, Verdict.CLEAN], Verdict.CLEAN),
    ],
)
def test_aggregate(verdicts: list[Verdict], expected: Verdict) -> None:
    assert aggregate(EngineResult("e", v) for v in verdicts) is expected


def test_fake_engine(tmp_path: Path) -> None:
    eicar = tmp_path / "eicar.com"
    eicar.write_bytes(EICAR)
    clean = tmp_path / "clean.txt"
    clean.write_text("hello")
    engine = FakeEngine()
    with file_info(eicar) as info:
        result = engine.scan(info)
    assert result.verdict is Verdict.MALICIOUS
    assert result.detections == ("EICAR-Test-File",)
    assert result.detail == "EICAR-Test-File"
    with file_info(clean) as info:
        assert engine.scan(info).verdict is Verdict.CLEAN


def test_engine_spec() -> None:
    engine = EngineSpec("fake", FakeEngine, (0.5,)).create()
    assert isinstance(engine, FakeEngine)
    assert engine.delay == 0.5


def test_result_detail() -> None:
    assert EngineResult("e", Verdict.ERROR, error="timeout").detail == "timeout"
    assert EngineResult("e", Verdict.SKIPPED, reason="known file").detail == "known file"
    assert FileInfo("docs/a.txt", 1, "", "", "").name == "a.txt"
