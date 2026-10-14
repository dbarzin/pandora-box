"""Common interface implemented by every detection engine."""

from __future__ import annotations

import posixpath
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar


class Verdict(StrEnum):
    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    MALICIOUS = "malicious"
    ERROR = "error"
    SKIPPED = "skipped"


class EngineKind(StrEnum):
    # Hash engines only look at the hashes of FileInfo and always run first
    HASH = "hash"
    # Content engines read the file through FileInfo.fd
    CONTENT = "content"


# Highest priority first: the aggregated verdict is the most severe one
_SEVERITY = (Verdict.MALICIOUS, Verdict.SUSPICIOUS, Verdict.ERROR, Verdict.CLEAN, Verdict.SKIPPED)

Fact = str | bool | int | float


@dataclass(frozen=True)
class FileInfo:
    """A file of the device, described once before the engines run.

    Engines must not recompute hashes or file types, and must never reopen the
    file by its path: they read it through fd, a read-only descriptor that is
    only valid during the call to Engine.scan().
    """

    rel_path: str  # relative to the mount point; may contain surrogate escapes
    size: int
    sha256: str
    sha1: str
    md5: str
    mime: str | None = None
    description: str | None = None
    fd: int = field(default=-1, repr=False, compare=False)

    @property
    def name(self) -> str:
        return posixpath.basename(self.rel_path)


@dataclass(frozen=True)
class EngineResult:
    engine: str
    verdict: Verdict
    # Signature or rule names that matched
    detections: tuple[str, ...] = ()
    error: str | None = None
    # Why the engine did not scan the file (SKIPPED verdict)
    reason: str | None = None
    # Auditable facts reported by the engine, e.g. {"known": True}
    facts: Mapping[str, Fact] = field(default_factory=dict)
    duration: float = 0.0

    @property
    def detail(self) -> str:
        """One line summary for logs and the user interface."""
        if self.detections:
            return ", ".join(self.detections)
        return self.error or self.reason or ""


@dataclass(frozen=True)
class SignatureInfo:
    """Version of a signature database or rule set used by an engine."""

    name: str
    version: str = ""
    date: datetime | None = None
    path: str = ""
    source: str = ""


class EngineError(Exception):
    """Raised when an engine cannot be loaded."""


class Engine(ABC):
    """A detection engine.

    Engines work offline, are loaded once at startup (load) and keep no state
    between files: each scan worker process builds its own instances.
    """

    name: ClassVar[str]
    kind: ClassVar[EngineKind] = EngineKind.CONTENT
    # Maximum scan time of one file, in seconds (enforced by the scan watchdog)
    timeout: float = 60.0

    def load(self) -> None:  # noqa: B027  (optional: not every engine has data to load)
        """Load signatures; raise EngineError when the engine cannot work."""

    def version(self) -> str:
        return ""

    def signature_info(self) -> list[SignatureInfo]:
        return []

    def extra_info(self) -> dict[str, Any]:
        """Engine specific information for the scan report (JSON values)."""
        return {}

    @abstractmethod
    def scan(self, file: FileInfo) -> EngineResult: ...


@dataclass(frozen=True)
class EngineSpec:
    """Picklable recipe to build an engine in a scan worker process.

    factory must be importable by name (a module level class or function).
    """

    name: str
    factory: Callable[..., Engine]
    args: tuple[Any, ...] = ()

    def create(self) -> Engine:
        return self.factory(*self.args)

    def factory_kind(self) -> EngineKind:
        kind = getattr(self.factory, "kind", EngineKind.CONTENT)
        return kind if isinstance(kind, EngineKind) else EngineKind.CONTENT


def aggregate(results: Iterable[EngineResult]) -> Verdict:
    """Combine engine results: a single positive engine is enough.

    No result at all is an error: a file is never clean by default.
    """
    verdicts = {r.verdict for r in results}
    if not verdicts:
        return Verdict.ERROR
    for verdict in _SEVERITY:
        if verdict in verdicts:
            return verdict
    return Verdict.ERROR
