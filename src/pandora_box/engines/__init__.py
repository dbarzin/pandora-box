"""Detection engines."""

from pandora_box.engines.base import (
    Engine,
    EngineError,
    EngineKind,
    EngineResult,
    EngineSpec,
    FileInfo,
    SignatureInfo,
    Verdict,
    aggregate,
)
from pandora_box.engines.fake import FakeEngine

__all__ = [
    "Engine",
    "EngineError",
    "EngineKind",
    "EngineResult",
    "EngineSpec",
    "FakeEngine",
    "FileInfo",
    "SignatureInfo",
    "Verdict",
    "aggregate",
]
