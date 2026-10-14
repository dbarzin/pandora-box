"""ClamAV engine, through the clamd daemon.

clamd must run with "AlertExceedsMax yes": without it, content beyond its
limits (MaxFileSize, MaxScanSize, MaxRecursion...) is reported as clean
without being scanned. With it, clamd reports Heuristics.Limits.Exceeded.*,
which is mapped to an error (engines.clamav.error_names).
"""

from __future__ import annotations

import contextlib
import fnmatch
from collections.abc import Sequence
from pathlib import Path

from pandora_box.clamd import ClamdClient, ClamdError, ClamdVersion, Reply, Status
from pandora_box.engines.base import (
    Engine,
    EngineError,
    EngineResult,
    FileInfo,
    SignatureInfo,
    Verdict,
)

MODES = ("auto", "fildes", "instream")


class ClamavEngine(Engine):
    name = "clamav"

    def __init__(
        self,
        socket_path: Path,
        mode: str = "auto",
        timeout: float = 120.0,
        max_file_size: int = 100 * 1024**2,
        suspicious_names: Sequence[str] = ("PUA.*", "Heuristics.*"),
        error_names: Sequence[str] = ("Heuristics.Limits.Exceeded.*",),
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"invalid clamd mode: {mode}")
        self.client = ClamdClient(socket_path, timeout)
        self.mode = mode
        self.timeout = timeout
        self.max_file_size = max_file_size
        self.suspicious_names = tuple(suspicious_names)
        self.error_names = tuple(error_names)
        self._version: ClamdVersion | None = None

    def load(self) -> None:
        # clamd loads its own databases, from the signed signature set
        try:
            self.client.ping()
            self._version = self.client.version()
        except (ClamdError, OSError) as ex:
            raise EngineError(f"clamav: {ex}") from ex

    def version(self) -> str:
        return self._version.engine if self._version else ""

    def signature_info(self) -> list[SignatureInfo]:
        # clamd reloads its databases while running: ask again
        with contextlib.suppress(ClamdError, OSError):
            self._version = self.client.version()
        if self._version is None:
            return []
        return [
            SignatureInfo(
                name="clamav",
                version=self._version.database,
                date=self._version.database_date,
                source=f"clamd {self.client.socket_path}",
            )
        ]

    def scan(self, file: FileInfo) -> EngineResult:
        if file.size > self.max_file_size:
            return self._error("file larger than engines.clamav.max_file_size (clamd limits)")
        facts: dict[str, str | bool | int | float] = {}
        try:
            if self.mode == "instream":
                replies = self.client.scan_stream(file.fd)
                facts["mode"] = "instream"
            else:
                replies = self.client.scan_fd(file.fd)
                facts["mode"] = "fildes"
                errors = [r for r in replies if r.status is Status.ERROR]
                if errors and self.mode == "auto":
                    # clamd could not use the descriptor: send the content instead
                    facts["fildes_error"] = errors[0].text
                    replies = self.client.scan_stream(file.fd)
                    facts["mode"] = "instream"
        except (ClamdError, OSError) as ex:
            return self._error(str(ex))
        return self._result(replies, facts)

    def _result(
        self, replies: list[Reply], facts: dict[str, str | bool | int | float]
    ) -> EngineResult:
        errors = [r.text for r in replies if r.status is Status.ERROR]
        found = [r.text for r in replies if r.status is Status.FOUND]
        incomplete = [n for n in found if _matches(n, self.error_names)]
        detections = [n for n in found if n not in incomplete]
        suspicious = [n for n in detections if _matches(n, self.suspicious_names)]
        malicious = [n for n in detections if n not in suspicious]
        if malicious:
            verdict = Verdict.MALICIOUS
        elif suspicious:
            verdict = Verdict.SUSPICIOUS
        elif errors or incomplete:
            return EngineResult(
                self.name,
                Verdict.ERROR,
                tuple(incomplete),
                error="; ".join(errors) or f"not fully scanned: {', '.join(incomplete)}",
                facts=facts,
            )
        else:
            return EngineResult(self.name, Verdict.CLEAN, facts=facts)
        return EngineResult(self.name, verdict, tuple(found), facts=facts)

    def _error(self, message: str) -> EngineResult:
        return EngineResult(self.name, Verdict.ERROR, error=message)


def _matches(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)
