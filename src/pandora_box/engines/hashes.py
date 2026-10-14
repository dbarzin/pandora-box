"""Hash engines: offline lookups of the file hashes, without reading the file."""

from __future__ import annotations

from pathlib import Path

from pandora_box.bloom import BloomError, BloomFilter
from pandora_box.engines.base import (
    Engine,
    EngineError,
    EngineKind,
    EngineResult,
    FileInfo,
    SignatureInfo,
    Verdict,
)
from pandora_box.hashdb import HashDatabase, HashDatabaseError
from pandora_box.signatures import describe_file

MALWAREBAZAAR_DETECTION = "MalwareBazaar.KnownMalware"


class MalwareBazaarEngine(Engine):
    """Known malicious SHA-256 hashes (abuse.ch MalwareBazaar)."""

    name = "malwarebazaar"
    kind = EngineKind.HASH
    timeout = 10.0

    def __init__(self, database: Path) -> None:
        self.database = database
        self._db: HashDatabase | None = None

    def load(self) -> None:
        try:
            self._db = HashDatabase(self.database)
        except HashDatabaseError as ex:
            raise EngineError(f"malwarebazaar: {ex}") from ex

    def version(self) -> str:
        return "1"

    def signature_info(self) -> list[SignatureInfo]:
        if self._db is None:
            return []
        # The database records its creation time, more reliable than the mtime
        return [
            describe_file(
                self.database,
                "abuse.ch MalwareBazaar",
                f"{self._db.count} hashes",
                self._db.created,
            )
        ]

    def scan(self, file: FileInfo) -> EngineResult:
        if self._db is None:
            raise EngineError("malwarebazaar: not loaded")
        if self._db.contains_hex(file.sha256):
            return EngineResult(self.name, Verdict.MALICIOUS, (MALWAREBAZAAR_DETECTION,))
        return EngineResult(self.name, Verdict.CLEAN)


class HashlookupEngine(Engine):
    """Known files (CIRCL hashlookup Bloom filter of SHA-1 hashes).

    A known file is not a benign file: the hashlookup sources (NSRL, Linux
    distributions...) also contain offensive tools. The answer is reported as
    the "known" fact; the scan pipeline decides what to do with it. The Bloom
    filter has false positives: an unknown file may be reported as known.
    """

    name = "hashlookup"
    kind = EngineKind.HASH
    timeout = 10.0

    def __init__(self, bloom: Path) -> None:
        self.bloom = bloom
        self._filter: BloomFilter | None = None

    def load(self) -> None:
        try:
            self._filter = BloomFilter(self.bloom)
        except BloomError as ex:
            raise EngineError(f"hashlookup: {ex}") from ex

    def version(self) -> str:
        return "DCSO bloom v1"

    def signature_info(self) -> list[SignatureInfo]:
        if self._filter is None:
            return []
        return [describe_file(self.bloom, "CIRCL hashlookup", f"{self._filter.count} hashes")]

    def scan(self, file: FileInfo) -> EngineResult:
        if self._filter is None:
            raise EngineError("hashlookup: not loaded")
        # hashlookup stores SHA-1 hashes in upper case hexadecimal
        known = file.sha1.upper().encode("ascii") in self._filter
        return EngineResult(
            self.name,
            Verdict.CLEAN,
            facts={"known": known, "fp_rate": self._filter.fp_rate},
        )
