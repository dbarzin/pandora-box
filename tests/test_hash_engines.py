from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import flor
import pytest

from pandora_box.bloom import BloomError, BloomFilter, write_filter
from pandora_box.config import parse_config
from pandora_box.engines import Engine, EngineError, EngineResult, FakeEngine, FileInfo, Verdict
from pandora_box.engines.hashes import (
    MALWAREBAZAAR_DETECTION,
    HashlookupEngine,
    MalwareBazaarEngine,
)
from pandora_box.engines.registry import NoEngineError, engine_specs, load_engines
from pandora_box.hashdb import HashDatabase, HashDatabaseError, main, parse_export, write_database
from pandora_box.pipeline import KNOWN_FILE, PipelineOptions, run_engines

MALWARE = b"pretend malware sample"
KNOWN = b"a well known system file"
UNKNOWN = b"a new document"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def info(data: bytes) -> FileInfo:
    return FileInfo("f", len(data), sha256(data), sha1(data), hashlib.md5(data).hexdigest())


# -- MalwareBazaar database ----------------------------------------------------


@pytest.fixture
def mb_database(tmp_path: Path) -> Path:
    path = tmp_path / "mb.bin"
    digests = {bytes.fromhex(sha256(MALWARE))} | {bytes([i]) * 32 for i in range(50)}
    write_database(digests, path, sha256(b"source"), created=1_700_000_000)
    return path


def test_database_lookup(mb_database: Path) -> None:
    db = HashDatabase(mb_database)
    assert db.count == 51
    assert db.contains_hex(sha256(MALWARE))
    assert db.contains_hex(sha256(MALWARE).upper())
    assert not db.contains_hex(sha256(UNKNOWN))
    assert not db.contains_hex("not hex")
    assert bytes([0]) * 32 in db and bytes([49]) * 32 in db
    assert bytes([255]) * 32 not in db
    assert db.created == datetime.fromtimestamp(1_700_000_000, UTC)
    assert db.source_sha256 == sha256(b"source")


def test_empty_database(tmp_path: Path) -> None:
    write_database([], tmp_path / "empty.bin")
    assert not HashDatabase(tmp_path / "empty.bin").contains_hex(sha256(MALWARE))


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda b: b[:10], "truncated"),
        (lambda b: b"XXXXXXXX" + b[8:], "not a PandoraBox hash database"),
        (lambda b: b[:-1], "size does not match"),
        (lambda b: b[:64] + b[96:128] + b[64:96] + b[128:], "not sorted"),
    ],
)
def test_corrupt_database(mb_database: Path, damage: object, message: str) -> None:
    data = mb_database.read_bytes()
    mb_database.write_bytes(damage(data))  # type: ignore[operator]
    with pytest.raises(HashDatabaseError, match=message):
        HashDatabase(mb_database)


def test_parse_export() -> None:
    lines = [
        "################################################################",
        "# MalwareBazaar full data dump (SHA256 hashes)                 #",
        "",
        sha256(MALWARE),
        f"  {sha256(KNOWN).upper()}  ",
        sha256(MALWARE),
    ]
    assert parse_export(lines) == {bytes.fromhex(sha256(MALWARE)), bytes.fromhex(sha256(KNOWN))}
    with pytest.raises(HashDatabaseError, match="line 2: not a SHA-256"):
        parse_export([sha256(MALWARE), "<html>"])


def test_build_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    export = tmp_path / "full_sha256.zip"
    with zipfile.ZipFile(export, "w") as archive:
        archive.writestr("full_sha256.txt", f"# header\n{sha256(MALWARE)}\n")
    output = tmp_path / "mb.bin"
    assert main(["build", str(export), str(output)]) == 0
    assert HashDatabase(output).contains_hex(sha256(MALWARE))
    assert main(["info", str(output)]) == 0
    assert "1 hashes" in capsys.readouterr().out
    (tmp_path / "bad.txt").write_text("nope\n")
    assert main(["build", str(tmp_path / "bad.txt"), str(output)]) == 1


# -- Bloom filter ----------------------------------------------------------------


def test_bloom_reads_flor_filters(tmp_path: Path) -> None:
    """Our reader agrees with flor, the reference used by CIRCL tools."""
    reference = flor.BloomFilter(n=1000, p=0.001)
    values = [hashlib.sha1(i.to_bytes(2, "big")).hexdigest().upper().encode() for i in range(500)]
    for value in values:
        reference.add(value)
    with (tmp_path / "flor.bloom").open("wb") as f:
        reference.write(f)
    bloom = BloomFilter(tmp_path / "flor.bloom")
    assert all(v in bloom for v in values)
    others = [hashlib.sha1(i.to_bytes(3, "big")).hexdigest().upper().encode() for i in range(2000)]
    assert [v in bloom for v in others] == [v in reference for v in others]
    assert bloom.count == 500 and bloom.capacity == 1000 and bloom.fp_rate == 0.001


def test_flor_reads_our_filters(tmp_path: Path) -> None:
    values = [b"A" * 40, b"B" * 40]
    write_filter(tmp_path / "ours.bloom", values)
    reference = flor.BloomFilter()
    with (tmp_path / "ours.bloom").open("rb") as f:
        reference.read(f)
    assert all(v in reference for v in values)
    assert b"C" * 40 not in reference


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"\x01" * 10, "truncated header"),
        (b"\x02" + b"\x00" * 47, "unsupported DCSO bloom version"),
        (b"\x01" + b"\x00" * 47, "invalid parameters"),
    ],
)
def test_bloom_invalid(tmp_path: Path, data: bytes, message: str) -> None:
    (tmp_path / "bad.bloom").write_bytes(data)
    with pytest.raises(BloomError, match=message):
        BloomFilter(tmp_path / "bad.bloom")


def test_bloom_truncated(tmp_path: Path) -> None:
    write_filter(tmp_path / "f.bloom", [b"x"])
    data = (tmp_path / "f.bloom").read_bytes()
    (tmp_path / "f.bloom").write_bytes(data[:-8])
    with pytest.raises(BloomError, match="truncated bit array"):
        BloomFilter(tmp_path / "f.bloom")


# -- Engines -------------------------------------------------------------------


@pytest.fixture
def bloom_path(tmp_path: Path) -> Path:
    path = tmp_path / "hashlookup.bloom"
    # Upper case hexadecimal SHA-1, like the CIRCL filter
    write_filter(path, [sha1(KNOWN).upper().encode(), sha1(MALWARE).upper().encode()])
    return path


def test_malwarebazaar_engine(mb_database: Path) -> None:
    engine = MalwareBazaarEngine(mb_database)
    engine.load()
    result = engine.scan(info(MALWARE))
    assert result.verdict is Verdict.MALICIOUS
    assert result.detections == (MALWAREBAZAAR_DETECTION,)
    assert engine.scan(info(UNKNOWN)).verdict is Verdict.CLEAN
    [signature] = engine.signature_info()
    assert signature.version == "51 hashes"
    assert signature.date == datetime.fromtimestamp(1_700_000_000, UTC)


def test_hashlookup_engine(bloom_path: Path) -> None:
    engine = HashlookupEngine(bloom_path)
    engine.load()
    known = engine.scan(info(KNOWN))
    assert known.verdict is Verdict.CLEAN
    assert known.facts["known"] is True
    assert known.facts["fp_rate"] == 0.001
    assert engine.scan(info(UNKNOWN)).facts["known"] is False


def test_signature_manifest(bloom_path: Path) -> None:
    (bloom_path.parent / "manifest.json").write_text(
        json.dumps({bloom_path.name: {"version": "2026-09", "date": "2026-09-01T00:00:00Z"}})
    )
    engine = HashlookupEngine(bloom_path)
    engine.load()
    [signature] = engine.signature_info()
    assert signature.version == "2026-09"
    assert signature.date == datetime(2026, 9, 1, tzinfo=UTC)
    assert signature.source == "CIRCL hashlookup"


@pytest.mark.parametrize("engine_class", [MalwareBazaarEngine, HashlookupEngine])
def test_missing_data(tmp_path: Path, engine_class: type[MalwareBazaarEngine]) -> None:
    with pytest.raises(EngineError, match="No such file"):
        engine_class(tmp_path / "missing").load()


# -- Ordering ------------------------------------------------------------------


class ContentEngine(Engine):
    name = "content"

    def scan(self, file: FileInfo) -> EngineResult:
        return EngineResult(self.name, Verdict.CLEAN)


class DetectingEngine(Engine):
    """A content engine that detects everything (EICAR for ClamAV)."""

    name = "content"

    def scan(self, file: FileInfo) -> EngineResult:
        return EngineResult(self.name, Verdict.MALICIOUS, ("Eicar-Signature",))


@pytest.fixture
def engines(mb_database: Path, bloom_path: Path) -> list[Engine]:
    mb = MalwareBazaarEngine(mb_database)
    hl = HashlookupEngine(bloom_path)
    mb.load()
    hl.load()
    # Content engine first: hash engines must still run first
    return [ContentEngine(), hl, mb]


def verdicts(results: list[EngineResult]) -> dict[str, Verdict]:
    return {r.engine: r.verdict for r in results}


def test_known_file_is_scanned_by_default(engines: list[Engine]) -> None:
    results = run_engines(info(KNOWN), engines, PipelineOptions())
    assert verdicts(results)["content"] is Verdict.CLEAN


def test_known_malicious_file_is_detected(engines: list[Engine]) -> None:
    # The EICAR test file is in the CIRCL filter: known is not benign
    engines = [DetectingEngine(), *engines[1:]]
    results = run_engines(info(KNOWN), engines, PipelineOptions())
    assert verdicts(results)["content"] is Verdict.MALICIOUS


def test_known_file_skips_content_engines_when_enabled(engines: list[Engine]) -> None:
    options = PipelineOptions(skip_content_for_known=True)
    results = run_engines(info(KNOWN), engines, options)
    assert [r.engine for r in results] == ["hashlookup", "malwarebazaar", "content"]
    assert results[2].verdict is Verdict.SKIPPED
    assert results[2].reason == KNOWN_FILE


def test_malicious_hash_wins_over_known(engines: list[Engine]) -> None:
    # MALWARE is in both the Bloom filter and MalwareBazaar
    results = run_engines(info(MALWARE), engines, PipelineOptions(skip_content_for_known=True))
    assert verdicts(results) == {
        "hashlookup": Verdict.CLEAN,
        "malwarebazaar": Verdict.MALICIOUS,
        "content": Verdict.CLEAN,
    }


def test_unknown_file_is_scanned(engines: list[Engine]) -> None:
    results = run_engines(info(UNKNOWN), engines, PipelineOptions())
    assert verdicts(results)["content"] is Verdict.CLEAN


# -- Registry ------------------------------------------------------------------


def test_hash_engines_alone_are_refused(mb_database: Path, bloom_path: Path) -> None:
    config = parse_config(
        {
            "engines": {
                "malwarebazaar": {"database": str(mb_database)},
                "hashlookup": {"bloom": str(bloom_path)},
                "clamav": {"enabled": False},
                "yara": {"enabled": False},
            }
        }
    )
    with pytest.raises(NoEngineError, match="no content engine"):
        engine_specs(config)


def test_fake_specs() -> None:
    config = parse_config({"kiosk": {"fake_scan": True}, "scan": {"fake_delay": 0.5}})
    [engine] = load_engines(engine_specs(config))
    assert isinstance(engine, FakeEngine) and engine.delay == 0.5
