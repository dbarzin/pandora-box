from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pandora_box.clamd import ClamdError, Reply, Status, parse_scan_replies, parse_version
from pandora_box.config import parse_config
from pandora_box.engines import EngineError, EngineResult, FileInfo, Verdict
from pandora_box.engines.clamav import ClamavEngine
from pandora_box.engines.registry import engine_specs

from .fake_clamd import fake_clamd
from .samples import eicar


@contextmanager
def file_info(tmp_path: Path, content: bytes) -> Iterator[FileInfo]:
    path = tmp_path / "sample"
    path.write_bytes(content)
    fd = os.open(path, os.O_RDONLY)
    try:
        yield FileInfo("sample", len(content), hashlib.sha256(content).hexdigest(), "", "", fd=fd)
    finally:
        os.close(fd)


def scan(
    tmp_path: Path,
    content: bytes,
    mode: str = "auto",
    server_mode: str = "normal",
    stream_max: int = 1024**2,
) -> tuple[EngineResult, list[str]]:
    with fake_clamd(server_mode, stream_max) as server:
        engine = ClamavEngine(server.path, mode=mode, timeout=1.0)
        if server_mode == "normal":
            engine.load()
        with file_info(tmp_path, content) as info:
            return engine.scan(info), server.commands


def test_parse_version() -> None:
    version = parse_version("ClamAV 1.4.3/27791/Thu Oct  2 09:12:00 2026")
    assert version.engine == "ClamAV 1.4.3"
    assert version.database == "27791"
    assert version.database_date is not None
    local = datetime(2026, 10, 2, 9, 12).astimezone(UTC)
    assert version.database_date == local
    assert parse_version("ClamAV 1.4.3") == parse_version("ClamAV 1.4.3")
    with pytest.raises(ClamdError):
        parse_version("Hello")


def test_parse_replies() -> None:
    assert parse_scan_replies("fd[10]: OK\0") == [Reply(Status.OK)]
    assert parse_scan_replies("stream: Win.Test FOUND\0") == [Reply(Status.FOUND, "Win.Test")]
    assert parse_scan_replies("INSTREAM size limit exceeded. ERROR\0") == [
        Reply(Status.ERROR, "INSTREAM size limit exceeded.")
    ]
    for bad in ("", "\0", "stream: OK maybe\0", "PONG\0", "stream: two words FOUND\0"):
        with pytest.raises(ClamdError):
            parse_scan_replies(bad)


def test_load_and_version() -> None:
    with fake_clamd() as server:
        engine = ClamavEngine(server.path)
        engine.load()
        assert engine.version() == "ClamAV 1.4.3"
        [signature] = engine.signature_info()
        assert signature.version == "27791"
        assert server.commands[:2] == ["PING", "VERSION"]


@pytest.mark.parametrize("mode", ["auto", "fildes", "instream"])
def test_clean_and_eicar(tmp_path: Path, mode: str) -> None:
    result, commands = scan(tmp_path, b"hello", mode)
    assert result.verdict is Verdict.CLEAN
    result, commands = scan(tmp_path, b"prefix " + eicar(), mode)
    assert result.verdict is Verdict.MALICIOUS
    assert result.detections == ("Eicar-Test-Signature",)
    expected = "INSTREAM" if mode == "instream" else "FILDES"
    assert commands[-1] == expected
    assert result.facts["mode"] == expected.lower()


def test_large_stream(tmp_path: Path) -> None:
    content = os.urandom(300_000) + eicar()
    result, _ = scan(tmp_path, content, "instream")
    assert result.verdict is Verdict.MALICIOUS


def test_suspicious_names(tmp_path: Path) -> None:
    result, _ = scan(tmp_path, b"PUA-TEST")
    assert result.verdict is Verdict.SUSPICIOUS
    assert result.detections == ("PUA.Win.Test",)


def test_all_match_scan(tmp_path: Path) -> None:
    result, _ = scan(tmp_path, b"MULTI-TEST")
    assert result.verdict is Verdict.MALICIOUS
    assert result.detections == ("PUA.Win.Test", "Win.Trojan.Test")


def test_limits_exceeded_is_an_error(tmp_path: Path) -> None:
    result, _ = scan(tmp_path, b"LIMITS-TEST")
    assert result.verdict is Verdict.ERROR
    assert result.error is not None and "Heuristics.Limits.Exceeded" in result.error


def test_stream_size_limit_is_an_error(tmp_path: Path) -> None:
    result, _ = scan(tmp_path, b"x" * 200_000, "instream", stream_max=100_000)
    assert result.verdict is Verdict.ERROR
    assert result.error == "INSTREAM size limit exceeded."


def test_kiosk_size_limit(tmp_path: Path) -> None:
    with fake_clamd() as server:
        engine = ClamavEngine(server.path, max_file_size=3)
        with file_info(tmp_path, b"hello") as info:
            result = engine.scan(info)
        assert result.verdict is Verdict.ERROR
        assert "max_file_size" in (result.error or "")
        assert "FILDES" not in server.commands


def test_fildes_fallback(tmp_path: Path) -> None:
    result, commands = scan(tmp_path, eicar(), "auto", "reject_fd")
    assert result.verdict is Verdict.MALICIOUS
    assert commands == ["FILDES", "INSTREAM"]
    assert result.facts["mode"] == "instream"
    assert "Permission denied" in str(result.facts["fildes_error"])


def test_fildes_only_does_not_fall_back(tmp_path: Path) -> None:
    result, commands = scan(tmp_path, eicar(), "fildes", "reject_fd")
    assert result.verdict is Verdict.ERROR
    assert commands == ["FILDES"]


@pytest.mark.parametrize("server_mode", ["hang", "garbage", "close"])
def test_bad_daemon_is_an_error(tmp_path: Path, server_mode: str) -> None:
    result, _ = scan(tmp_path, b"hello", "auto", server_mode)
    assert result.verdict is Verdict.ERROR
    assert result.error


def test_no_daemon(tmp_path: Path) -> None:
    engine = ClamavEngine(tmp_path / "missing.sock")
    with pytest.raises(EngineError, match="cannot connect"):
        engine.load()
    with file_info(tmp_path, b"x") as info:
        assert engine.scan(info).verdict is Verdict.ERROR


def test_load_refuses_bad_daemon() -> None:
    with fake_clamd("garbage") as server, pytest.raises(EngineError):
        ClamavEngine(server.path).load()


def test_spec_from_config() -> None:
    config = parse_config(
        {
            "engines": {
                "malwarebazaar": {"enabled": False},
                "hashlookup": {"enabled": False},
                "clamav": {"mode": "instream", "suspicious_names": []},
                "yara": {"enabled": False},
                "heuristics": {"enabled": False},
            }
        }
    )
    [spec] = engine_specs(config)
    engine = spec.create()
    assert isinstance(engine, ClamavEngine)
    assert engine.mode == "instream"
    assert engine.suspicious_names == ()


CLAMD_SOCKET = Path(os.environ.get("PANDORA_BOX_CLAMD_SOCKET", "/run/clamav/clamd.ctl"))


@pytest.mark.integration
@pytest.mark.skipif(not CLAMD_SOCKET.exists(), reason="no clamd socket")
@pytest.mark.parametrize("mode", ["fildes", "instream"])
def test_real_clamd(tmp_path: Path, mode: str) -> None:
    engine = ClamavEngine(CLAMD_SOCKET, mode=mode)
    engine.load()
    assert engine.version().startswith("ClamAV")
    with file_info(tmp_path, eicar()) as info:
        result = engine.scan(info)
    assert result.verdict is Verdict.MALICIOUS, result
    with file_info(tmp_path, b"harmless text\n") as info:
        assert engine.scan(info).verdict is Verdict.CLEAN
