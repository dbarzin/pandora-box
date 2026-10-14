"""Heuristics on the structure of files: PDF active content, disguised programs."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from pandora_box.engines import EngineResult, FileInfo, Verdict, heuristics
from pandora_box.engines.heuristics import HeuristicsEngine, disguised, pdf_names

PDF_HEAD = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"


@contextmanager
def opened(tmp_path: Path, content: bytes) -> Iterator[int]:
    path = tmp_path / "file"
    path.write_bytes(content)
    fd = os.open(path, os.O_RDONLY)
    try:
        yield fd
    finally:
        os.close(fd)


def scan(tmp_path: Path, content: bytes, name: str = "file.pdf", mime: str = "") -> EngineResult:
    with opened(tmp_path, content) as fd:
        info = FileInfo(name, len(content), "", "", "", mime=mime or None, fd=fd)
        return HeuristicsEngine().scan(info)


def test_pdf_with_javascript(tmp_path: Path) -> None:
    pdf = PDF_HEAD + b"1 0 obj << /Type /Catalog /OpenAction 2 0 R >> endobj\n"
    pdf += b"2 0 obj << /S /JavaScript /JS (app.alert(1)) >> endobj\n"
    result = scan(tmp_path, pdf)
    assert result.verdict is Verdict.SUSPICIOUS
    assert result.detections == ("Heuristics.PDF.JavaScript",)
    assert result.facts == {"pdf_OpenAction": 1}


def test_hidden_names_are_decoded(tmp_path: Path) -> None:
    pdf = PDF_HEAD + b"<< /S /J#61vaScript >> << /S /L#61#75nch /F (cmd.exe) >>"
    result = scan(tmp_path, pdf)
    assert result.detections == ("Heuristics.PDF.JavaScript", "Heuristics.PDF.Launch")


def test_pdf_without_active_content(tmp_path: Path) -> None:
    pdf = PDF_HEAD + b"<< /Type /Catalog /AcroForm 3 0 R /JSONData (x) /Names [] >>"
    result = scan(tmp_path, pdf)
    assert result.verdict is Verdict.CLEAN
    # /JSONData is not /JS
    assert result.facts == {"pdf_AcroForm": 1}


def test_not_a_pdf(tmp_path: Path) -> None:
    assert scan(tmp_path, b"/JavaScript in a text file", "notes.txt").verdict is Verdict.CLEAN


def test_names_across_chunks_are_counted_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(heuristics, "_CHUNK", 300)
    monkeypatch.setattr(heuristics, "_OVERLAP", 100)
    # One name in each position around the chunk boundaries
    content = b"".join(b" " * gap + b"/JavaScript" for gap in range(0, 900, 37))
    expected = content.count(b"/JavaScript")
    with opened(tmp_path, content) as fd:
        assert pdf_names(fd) == {"JavaScript": expected}


@pytest.mark.parametrize(
    ("name", "mime", "findings"),
    [
        ("facture.pdf", "application/x-dosexec", ["Heuristics.Executable.Disguised"]),
        ("photo.JPG", "application/x-executable", ["Heuristics.Executable.Disguised"]),
        ("setup.exe", "application/x-dosexec", []),
        ("lib.so", "application/x-sharedlib", []),
        ("tool", "application/x-executable", []),
        ("facture.pdf.exe", "application/x-dosexec", ["Heuristics.DoubleExtension"]),
        ("rapport.docx.js", "text/plain", ["Heuristics.DoubleExtension"]),
        ("archive.tar.gz", "application/gzip", []),
        ("facture.pdf", "application/pdf", []),
    ],
)
def test_disguised_programs(name: str, mime: str, findings: list[str]) -> None:
    assert disguised(name, mime) == findings


def test_disguised_program_is_suspicious(tmp_path: Path) -> None:
    result = scan(tmp_path, b"MZ\x90\x00", "facture.pdf", "application/x-dosexec")
    assert result.verdict is Verdict.SUSPICIOUS
    assert result.detections == ("Heuristics.Executable.Disguised",)
