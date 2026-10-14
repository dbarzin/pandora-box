"""Messages sent by the scan workers to the kiosk process.

A scan worker parses hostile files: it must be assumed compromised. Its
messages are therefore JSON, never pickle (unpickling runs code), limited in
size, and strictly validated here before they become kiosk objects. Facts the
kiosk already knows (the path and size of the file, the timeout of an engine)
are never taken from a worker.

Messages from the kiosk to a worker are pickled: the worker trusts the kiosk.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pandora_box.engines import (
    Engine,
    EngineKind,
    EngineResult,
    FileInfo,
    SignatureInfo,
    Verdict,
)
from pandora_box.engines.base import Fact
from pandora_box.inventory import Entry
from pandora_box.results import FileResult

# Largest message accepted from a worker (a file result with its detections)
MAX_MESSAGE_SIZE = 4 * 1024 * 1024
# Longest string and list accepted in a message
MAX_STRING = 64 * 1024
MAX_ITEMS = 10_000


class ProtocolError(Exception):
    """A worker message is malformed: the worker is treated as crashed."""


@dataclass(frozen=True)
class EngineInfo:
    """Description of a loaded engine, for logs and scan reports."""

    name: str
    kind: EngineKind
    version: str
    timeout: float
    signatures: tuple[SignatureInfo, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)


def describe_engine(engine: Engine) -> EngineInfo:
    return EngineInfo(
        name=engine.name,
        kind=engine.kind,
        version=engine.version(),
        timeout=engine.timeout,
        signatures=tuple(engine.signature_info()),
        extra=engine.extra_info(),
    )


# -- worker side: encoding ---------------------------------------------------------


def encode(message: Mapping[str, Any]) -> bytes:
    # ensure_ascii keeps undecodable file names (surrogate escapes) encodable
    return json.dumps(message, default=_default).encode("ascii")


def _default(value: object) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot encode {type(value).__name__}")


def engine_info_message(engines: list[EngineInfo]) -> list[dict[str, Any]]:
    return [
        {
            "name": e.name,
            "kind": e.kind.value,
            "version": e.version,
            "timeout": e.timeout,
            "signatures": [
                {
                    "name": s.name,
                    "version": s.version,
                    "date": s.date,
                    "path": s.path,
                    "source": s.source,
                }
                for s in e.signatures
            ],
            "extra": e.extra,
        }
        for e in engines
    ]


def engine_result_message(result: EngineResult) -> dict[str, Any]:
    """The result of the engine of a worker for one file."""
    return {
        "engine": result.engine,
        "verdict": result.verdict.value,
        "detections": list(result.detections),
        "error": result.error,
        "reason": result.reason,
        "facts": dict(result.facts),
        "duration": result.duration,
    }


def result_message(result: FileResult) -> dict[str, Any]:
    """A whole file result (tests and tools)."""
    info = result.info
    return {
        "verdict": result.verdict.value,
        "detail": result.detail,
        "duration": result.duration,
        "results": [engine_result_message(r) for r in result.results],
        "info": None
        if info is None
        else {
            "sha256": info.sha256,
            "sha1": info.sha1,
            "md5": info.md5,
            "mime": info.mime,
            "description": info.description,
        },
    }


# -- kiosk side: decoding ------------------------------------------------------------


def decode(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_MESSAGE_SIZE:
        raise ProtocolError("message too long")
    try:
        message = json.loads(data.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as ex:
        raise ProtocolError(f"invalid message: {ex}") from ex
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("invalid message")
    return message


def decode_engines(value: object) -> list[EngineInfo]:
    engines = []
    for item in _list(value):
        data = _dict(item)
        engines.append(
            EngineInfo(
                name=_str(data.get("name")),
                kind=_enum(EngineKind, data.get("kind")),
                version=_str(data.get("version")),
                timeout=_number(data.get("timeout")),
                signatures=tuple(_signature(s) for s in _list(data.get("signatures"))),
                extra=_json_object(data.get("extra")),
            )
        )
    return engines


def decode_engine_result(value: object, engine: str) -> EngineResult:
    """The result of a worker for its own engine, and no other."""
    r = _dict(value)
    if _str(r.get("engine")) != engine:
        raise ProtocolError(f"result of another engine: {_str(r.get('engine'))[:100]}")
    return EngineResult(
        engine=engine,
        verdict=_enum(Verdict, r.get("verdict")),
        detections=tuple(_str(d) for d in _list(r.get("detections"))),
        error=_optional_str(r.get("error")),
        reason=_optional_str(r.get("reason")),
        facts=_facts(r.get("facts")),
        duration=_number(r.get("duration")),
    )


def decode_file_type(message: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Type of the file identified by a content worker (informational)."""
    return _optional_str(message.get("mime")), _optional_str(message.get("description"))


def decode_result(value: object, root: Path, entry: Entry, engines: set[str]) -> FileResult:
    """Build the result of a file: path and size come from the kiosk inventory."""
    data = _dict(value)
    results = []
    for item in _list(data.get("results")):
        r = _dict(item)
        name = _str(r.get("engine"))
        if name not in engines:
            raise ProtocolError(f"unknown engine: {name}")
        results.append(
            EngineResult(
                engine=name,
                verdict=_enum(Verdict, r.get("verdict")),
                detections=tuple(_str(d) for d in _list(r.get("detections"))),
                error=_optional_str(r.get("error")),
                reason=_optional_str(r.get("reason")),
                facts=_facts(r.get("facts")),
                duration=_number(r.get("duration")),
            )
        )
    info = None
    if data.get("info") is not None:
        i = _dict(data.get("info"))
        info = FileInfo(
            rel_path=entry.rel_path,
            size=entry.size,
            sha256=_hex(i.get("sha256"), 64),
            sha1=_hex(i.get("sha1"), 40),
            md5=_hex(i.get("md5"), 32),
            mime=_optional_str(i.get("mime")),
            description=_optional_str(i.get("description")),
        )
    return FileResult(
        path=root / entry.rel_path,
        size=entry.size,
        verdict=_enum(Verdict, data.get("verdict")),
        results=tuple(results),
        detail=_str(data.get("detail")),
        duration=_number(data.get("duration")),
        info=info,
        rel_path=entry.rel_path,
    )


def _signature(value: object) -> SignatureInfo:
    data = _dict(value)
    date = _optional_str(data.get("date"))
    try:
        parsed = datetime.fromisoformat(date) if date else None
    except ValueError as ex:
        raise ProtocolError(f"invalid date: {date}") from ex
    return SignatureInfo(
        name=_str(data.get("name")),
        version=_str(data.get("version")),
        date=parsed,
        path=_str(data.get("path")),
        source=_str(data.get("source")),
    )


def _dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("object expected")
    return value


def _list(value: object) -> list[Any]:
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise ProtocolError("list expected")
    return value


def _str(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_STRING:
        raise ProtocolError("string expected")
    return value


def _optional_str(value: object) -> str | None:
    return None if value is None else _str(value)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProtocolError("number expected")
    if not math.isfinite(value) or value < 0:
        raise ProtocolError("invalid number")
    return float(value)


def _hex(value: object, length: int) -> str:
    text = _str(value)
    if not re.fullmatch(f"[0-9a-f]{{{length}}}", text):
        raise ProtocolError("invalid hash")
    return text


_E = TypeVar("_E", Verdict, EngineKind)


def _enum(kind: type[_E], value: object) -> _E:
    try:
        return kind(_str(value))
    except ValueError as ex:
        raise ProtocolError(f"invalid {kind.__name__}: {value}") from ex


def _facts(value: object) -> dict[str, Fact]:
    facts: dict[str, Fact] = {}
    for key, fact in _dict(value).items():
        if isinstance(fact, str):
            fact = _str(fact)
        elif not isinstance(fact, bool | int | float):
            raise ProtocolError("invalid fact")
        facts[_str(key)] = fact
    return facts


def _json_object(value: object) -> dict[str, Any]:
    """Engine specific information: any JSON object, for the scan report."""
    return _dict(value)
