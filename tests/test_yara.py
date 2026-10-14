from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from pandora_box.config import YaraConfig, YaraRuleSet, parse_config
from pandora_box.engines import EngineError, EngineResult, FileInfo, Verdict
from pandora_box.engines.registry import engine_specs
from pandora_box.engines.yara import YaraEngine, externals

RULES = """
rule Test_Malicious {
    meta: score = 90
    strings: $a = "MALICIOUS-MARKER"
    condition: $a
}
rule Test_Suspicious {
    meta: score = 50
    strings: $a = "SUSPICIOUS-MARKER"
    condition: $a
}
rule Test_Informational {
    meta: score = 10
    strings: $a = "INFO-MARKER"
    condition: $a
}
rule Test_No_Score {
    strings: $a = "NOSCORE-MARKER"
    condition: $a
}
rule Test_String_Score {
    meta: score = "80"
    strings: $a = "STRSCORE-MARKER"
    condition: $a
}
rule Test_Externals {
    meta: score = 75
    condition: filename == "evil.exe" and extension == ".exe"
        and filepath == "/dir/evil.exe" and filetype == "EXE" and owner == ""
}
"""

BROKEN = """
rule Good_In_Broken_File {
    meta: score = 90
    strings: $a = "GOOD-MARKER"
    condition: $a
}
rule Broken { condition: unknown_identifier }
"""

SLOW = """
rule Slow {
    condition: for all i in (0..filesize) : (for all j in (0..filesize) : (i + j >= 0))
}
"""


@pytest.fixture
def rules_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "rules"
    folder.mkdir()
    (folder / "test.yar").write_text(RULES)
    return folder


def make_engine(rules: Path, tmp_path: Path, **options: object) -> YaraEngine:
    defaults: dict[str, object] = {
        "rules": (YaraRuleSet("test", rules),),
        "cache_dir": tmp_path / "cache",
        "timeout": 5.0,
    }
    defaults.update(options)
    engine = YaraEngine(YaraConfig(**defaults))  # type: ignore[arg-type]
    engine.load()
    return engine


@contextmanager
def file_info(tmp_path: Path, content: bytes, rel_path: str = "file.bin") -> Iterator[FileInfo]:
    path = tmp_path / "sample"
    path.write_bytes(content)
    fd = os.open(path, os.O_RDONLY)
    try:
        yield FileInfo(rel_path, len(content), hashlib.sha256(content).hexdigest(), "", "", fd=fd)
    finally:
        os.close(fd)


def scan(engine: YaraEngine, tmp_path: Path, content: bytes) -> EngineResult:
    with file_info(tmp_path, content) as info:
        return engine.scan(info)


@pytest.mark.parametrize(
    ("content", "verdict", "detections"),
    [
        (b"nothing here", Verdict.CLEAN, ()),
        (b"x MALICIOUS-MARKER x", Verdict.MALICIOUS, ("test:Test_Malicious",)),
        (b"SUSPICIOUS-MARKER", Verdict.SUSPICIOUS, ("test:Test_Suspicious",)),
        (b"NOSCORE-MARKER", Verdict.SUSPICIOUS, ("test:Test_No_Score",)),
        (b"STRSCORE-MARKER", Verdict.MALICIOUS, ("test:Test_String_Score",)),
        (
            b"MALICIOUS-MARKER SUSPICIOUS-MARKER",
            Verdict.MALICIOUS,
            ("test:Test_Malicious", "test:Test_Suspicious"),
        ),
    ],
)
def test_score_mapping(
    rules_dir: Path, tmp_path: Path, content: bytes, verdict: Verdict, detections: tuple[str, ...]
) -> None:
    result = scan(make_engine(rules_dir, tmp_path), tmp_path, content)
    assert result.verdict is verdict
    assert result.detections == detections


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_scans_the_descriptor_of_a_file_it_cannot_open(rules_dir: Path, tmp_path: Path) -> None:
    # On ext4, files private to their owner: the kiosk opens them, the
    # sandboxed worker could not
    engine = make_engine(rules_dir, tmp_path)
    with file_info(tmp_path, b"x MALICIOUS-MARKER x") as info:
        (tmp_path / "sample").chmod(0)
        result = engine.scan(info)
    assert result.verdict is Verdict.MALICIOUS, result


def test_informational_match(rules_dir: Path, tmp_path: Path) -> None:
    result = scan(make_engine(rules_dir, tmp_path), tmp_path, b"INFO-MARKER")
    assert result.verdict is Verdict.CLEAN
    assert result.facts["informational"] == "test:Test_Informational"


def test_thresholds(rules_dir: Path, tmp_path: Path) -> None:
    engine = make_engine(rules_dir, tmp_path, malicious_score=95, default_score=20)
    assert scan(engine, tmp_path, b"MALICIOUS-MARKER").verdict is Verdict.SUSPICIOUS
    assert scan(engine, tmp_path, b"NOSCORE-MARKER").verdict is Verdict.CLEAN


def test_externals(rules_dir: Path, tmp_path: Path) -> None:
    info = FileInfo("dir/Evil.EXE", 1, "", "", "", mime="application/x-dosexec")
    assert externals(info) == {
        "filename": "Evil.EXE",
        "filepath": "/dir/Evil.EXE",
        "extension": ".exe",
        "filetype": "EXE",
        "owner": "",
    }
    engine = make_engine(rules_dir, tmp_path)
    (tmp_path / "sample").write_bytes(b"MZ")
    fd = os.open(tmp_path / "sample", os.O_RDONLY)
    try:
        info = FileInfo("dir/evil.exe", 2, "", "", "", mime="application/x-dosexec", fd=fd)
        assert engine.scan(info).detections == ("test:Test_Externals",)
        # Externals are reset for each file
        other = FileInfo("dir/other.exe", 2, "", "", "", mime="application/x-dosexec", fd=fd)
        assert engine.scan(other).verdict is Verdict.CLEAN
    finally:
        os.close(fd)


def test_compile_error_fails(rules_dir: Path, tmp_path: Path) -> None:
    (rules_dir / "broken.yar").write_text(BROKEN)
    with pytest.raises(EngineError, match=r"(?s)1 compile errors.*broken\.yar:7: E009"):
        make_engine(rules_dir, tmp_path)


def test_compile_error_skip_rule(rules_dir: Path, tmp_path: Path) -> None:
    (rules_dir / "broken.yar").write_text(BROKEN)
    engine = make_engine(rules_dir, tmp_path, on_compile_error="skip_rule")
    assert len(engine.excluded_rules) == 1
    assert "broken.yar:7: E009" in engine.excluded_rules[0]
    assert engine.extra_info()["excluded_rules"] == engine.excluded_rules
    # Valid rules of the same file are kept
    assert scan(engine, tmp_path, b"GOOD-MARKER").verdict is Verdict.MALICIOUS


def test_syntax_error_skip_rule(rules_dir: Path, tmp_path: Path) -> None:
    (rules_dir / "syntax.yar").write_text("rule Unfinished { condition: ")
    engine = make_engine(rules_dir, tmp_path, on_compile_error="skip_rule")
    assert "E001" in engine.excluded_rules[0]
    assert scan(engine, tmp_path, b"MALICIOUS-MARKER").verdict is Verdict.MALICIOUS


def test_exclude(rules_dir: Path, tmp_path: Path) -> None:
    (rules_dir / "broken.yar").write_text(BROKEN)
    engine = make_engine(rules_dir, tmp_path, exclude=("*/broken.yar",))
    assert engine.excluded_rules == []


def test_namespaces(rules_dir: Path, tmp_path: Path) -> None:
    # The same rule name in two files does not conflict
    (rules_dir / "sub").mkdir()
    (rules_dir / "sub" / "copy.yara").write_text(RULES)
    engine = make_engine(rules_dir, tmp_path)
    assert engine.rule_count == 12
    result = scan(engine, tmp_path, b"MALICIOUS-MARKER")
    assert result.detections == ("test:Test_Malicious", "test:Test_Malicious")


def test_missing_rules(tmp_path: Path) -> None:
    with pytest.raises(EngineError, match="not found"):
        make_engine(tmp_path / "missing", tmp_path)
    (tmp_path / "empty").mkdir()
    with pytest.raises(EngineError, match="no rule file"):
        make_engine(tmp_path / "empty", tmp_path)


def test_cache(rules_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (rules_dir / "broken.yar").write_text(BROKEN)
    first = make_engine(rules_dir, tmp_path, on_compile_error="skip_rule")
    files = sorted((tmp_path / "cache").iterdir())
    assert len(files) == 2
    assert all(f.stat().st_mode & 0o077 == 0 for f in files)

    def no_compile(self: YaraEngine) -> None:
        raise AssertionError("compiled again")

    monkeypatch.setattr(YaraEngine, "_compile", no_compile)
    second = make_engine(rules_dir, tmp_path, on_compile_error="skip_rule")
    assert second.rule_count == first.rule_count
    assert second.excluded_rules == first.excluded_rules
    assert scan(second, tmp_path, b"MALICIOUS-MARKER").verdict is Verdict.MALICIOUS

    # A changed rule file invalidates the cache
    (rules_dir / "test.yar").write_text(RULES + "\nrule Extra { condition: false }\n")
    with pytest.raises(AssertionError, match="compiled again"):
        make_engine(rules_dir, tmp_path, on_compile_error="skip_rule")


def test_damaged_cache_is_rebuilt(rules_dir: Path, tmp_path: Path) -> None:
    make_engine(rules_dir, tmp_path)
    for path in (tmp_path / "cache").glob("*.rules"):
        path.write_bytes(b"garbage")
    engine = make_engine(rules_dir, tmp_path)
    assert scan(engine, tmp_path, b"MALICIOUS-MARKER").verdict is Verdict.MALICIOUS


def test_no_cache(rules_dir: Path, tmp_path: Path) -> None:
    make_engine(rules_dir, tmp_path, cache_dir=None)
    assert not (tmp_path / "cache").exists()


def test_timeout(tmp_path: Path) -> None:
    (tmp_path / "slow.yar").write_text(SLOW)
    engine = make_engine(tmp_path / "slow.yar", tmp_path, timeout=1.0)
    result = scan(engine, tmp_path, b"\0" * 1024 * 1024)
    assert result.verdict is Verdict.ERROR
    assert result.error is not None and "timeout" in result.error


def test_signature_info(rules_dir: Path, tmp_path: Path) -> None:
    engine = make_engine(rules_dir, tmp_path)
    [info] = engine.signature_info()
    assert info.name == "rules"
    assert info.source == "test"
    assert engine.version().startswith("YARA-X ")


def test_spec_from_config(rules_dir: Path) -> None:
    config = parse_config(
        {
            "engines": {
                "malwarebazaar": {"enabled": False},
                "hashlookup": {"enabled": False},
                "clamav": {"enabled": False},
                "heuristics": {"enabled": False},
                "yara": {"rules": [{"name": "t", "path": str(rules_dir)}], "cache_dir": ""},
            }
        }
    )
    [spec] = engine_specs(config)
    engine = spec.create()
    engine.load()
    assert isinstance(engine, YaraEngine) and engine.rule_count == 6


YARA_RULES = os.environ.get("PANDORA_BOX_YARA_RULES")


@pytest.mark.integration
@pytest.mark.skipif(not YARA_RULES, reason="PANDORA_BOX_YARA_RULES not set")
def test_real_rules(tmp_path: Path) -> None:
    """Compile real rule sets (e.g. YARA Forge) and scan harmless content."""
    rule_sets = tuple(
        YaraRuleSet(f"set{i}", Path(p)) for i, p in enumerate((YARA_RULES or "").split(":"))
    )
    engine = YaraEngine(
        YaraConfig(rules=rule_sets, cache_dir=tmp_path / "cache", on_compile_error="skip_rule")
    )
    engine.load()
    assert engine.rule_count > 0
    assert scan(engine, tmp_path, b"harmless text\n").verdict is Verdict.CLEAN
