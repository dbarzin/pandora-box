from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import pytest

from pandora_box.engines import EngineKind, EngineResult, FileInfo, Verdict
from pandora_box.inventory import Entry
from pandora_box.protocol import (
    MAX_MESSAGE_SIZE,
    EngineInfo,
    ProtocolError,
    decode,
    decode_engines,
    decode_result,
    encode,
    engine_info_message,
    result_message,
)
from pandora_box.results import FileResult

ENTRY = Entry("docs/a.txt", 3, 1, 2)
ROOT = Path("/media/pandora-box")


def worker_result() -> FileResult:
    return FileResult(
        path=Path("docs/a.txt"),
        size=3,
        verdict=Verdict.MALICIOUS,
        results=(
            EngineResult("clamav", Verdict.MALICIOUS, ("Eicar-Test",), facts={"mode": "fildes"}),
            EngineResult("yara", Verdict.ERROR, error="timeout \udcff"),
        ),
        detail="",
        duration=0.5,
        info=FileInfo("docs/a.txt", 3, "a" * 64, "b" * 40, "c" * 32, "text/plain", "ASCII"),
        rel_path="docs/a.txt",
    )


def roundtrip(result: dict[str, Any]) -> FileResult:
    message = decode(encode({"type": "result", "task": 1, "result": result}))
    return decode_result(message["result"], ROOT, ENTRY, {"clamav", "yara"})


def test_result_roundtrip() -> None:
    result = roundtrip(result_message(worker_result()))
    assert result.path == ROOT / "docs/a.txt"
    assert result.verdict is Verdict.MALICIOUS
    assert result.results[0].detections == ("Eicar-Test",)
    assert result.results[0].facts == {"mode": "fildes"}
    assert result.results[1].error == "timeout \udcff"
    assert result.info is not None
    assert result.info.sha256 == "a" * 64


def test_path_and_size_come_from_the_kiosk() -> None:
    message = result_message(worker_result())
    message["path"] = "/etc/shadow"
    message["size"] = 0
    result = roundtrip(message)
    assert result.path == ROOT / ENTRY.rel_path
    assert result.size == ENTRY.size
    assert result.rel_path == ENTRY.rel_path


def test_engine_info_roundtrip() -> None:
    engines = [EngineInfo("yara", EngineKind.CONTENT, "1.0", 60.0, (), {"rules": 3})]
    message = decode(encode({"type": "ready", "engines": engine_info_message(engines)}))
    assert decode_engines(message["engines"]) == engines


@pytest.mark.parametrize(
    "change",
    [
        {"verdict": "harmless"},
        {"verdict": 1},
        {"duration": float("nan")},
        {"duration": -1},
        {"detail": "x" * 100_000},
        {"results": [{"engine": "unknown-av", "verdict": "clean"}]},
        {"results": "clean"},
        {"info": {"sha256": "../../etc", "sha1": "b" * 40, "md5": "c" * 32}},
    ],
)
def test_invalid_results_are_refused(change: dict[str, Any]) -> None:
    message = result_message(worker_result()) | change
    with pytest.raises(ProtocolError):
        roundtrip(message)


def test_invalid_engine_fact_is_refused() -> None:
    message = result_message(worker_result())
    message["results"][0]["facts"] = {"mode": ["nested"]}
    with pytest.raises(ProtocolError):
        roundtrip(message)


@pytest.mark.parametrize(
    "data",
    [
        pickle.dumps(("result", 1, "clean")),
        b"[]",
        b'{"task": 1}',
        b"\xff\xfe",
        json.dumps({"type": "x", "padding": "x" * MAX_MESSAGE_SIZE}).encode(),
    ],
)
def test_invalid_messages_are_refused(data: bytes) -> None:
    with pytest.raises(ProtocolError):
        decode(data)
