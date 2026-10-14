"""YARA-X engine, with the YARA Forge and signature-base rule sets.

Rules are compiled once at startup, and the compiled rules are cached by a key
built from the YARA-X version, the rule files and the options. The cache holds
native code: it must be as trusted as the rules themselves (root only).

Rules that do not compile are never silently dropped: with on_compile_error =
"fail" the engine refuses to load, with "skip_rule" they are listed in the
logs and in every scan report.

The signature-base rules (written for LOKI and THOR) use external variables,
filled for each file: filename, filepath (full path on the device, starting
with "/"), extension (lower case, with the dot), filetype (THOR-like short
type derived from libmagic, an approximation) and owner (always empty: the
removable filesystems have no meaningful owner).

The "score" metadata of a matching rule (0-100, used by YARA Forge and
signature-base) gives the verdict: malicious from malicious_score, suspicious
from suspicious_score, informational below. Rules without a score get
default_score.
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import importlib.metadata
import io
import json
import logging
import os
import posixpath
from pathlib import Path
from typing import Any

import yara_x

from pandora_box.config import YaraConfig
from pandora_box.engines.base import (
    Engine,
    EngineError,
    EngineResult,
    FileInfo,
    SignatureInfo,
    Verdict,
)
from pandora_box.logs import get_logger, log_event
from pandora_box.signatures import describe_file

logger = get_logger("yara")

RULE_SUFFIXES = (".yar", ".yara")
EXTERNALS = ("filename", "filepath", "extension", "filetype", "owner")
_CACHE_FORMAT = 1
# Compile errors listed in the error message when the engine refuses to load
_MAX_LISTED_ERRORS = 20

# libmagic MIME type -> THOR file type, as used by signature-base conditions
FILETYPES = {
    "application/x-dosexec": "EXE",
    "application/vnd.microsoft.portable-executable": "EXE",
    "application/x-msdownload": "EXE",
    "application/x-executable": "ELF",
    "application/x-sharedlib": "ELF",
    "application/x-pie-executable": "ELF",
    "application/x-elf": "ELF",
    "application/x-mach-binary": "MACHO",
    "application/zip": "ZIP",
    "application/x-rar": "RAR",
    "application/x-7z-compressed": "7Z",
    "application/gzip": "GZIP",
    "application/x-tar": "TAR",
    "application/pdf": "PDF",
    "application/msword": "DOC",
    "application/vnd.ms-excel": "XLS",
    "application/vnd.ms-powerpoint": "PPT",
    "application/x-ole-storage": "OLE",
    "application/CDFV2": "OLE",
    "application/rtf": "RTF",
    "text/rtf": "RTF",
    "application/x-shockwave-flash": "SWF",
    "application/java-archive": "JAR",
    "application/x-java-applet": "CLASS",
    "application/vnd.ms-cab-compressed": "CAB",
    "application/x-iso9660-image": "ISO",
    "application/x-ms-shortcut": "LNK",
}


class YaraEngine(Engine):
    name = "yara"

    def __init__(self, config: YaraConfig) -> None:
        self.config = config
        self.timeout = config.timeout
        self._rules: yara_x.Rules | None = None
        self._scanner: yara_x.Scanner | None = None
        self.rule_count = 0
        # Compile errors, with on_compile_error = "skip_rule"
        self.excluded_rules: list[str] = []
        self._files: dict[str, list[Path]] = {}

    # -- loading -------------------------------------------------------------

    def load(self) -> None:
        self._files = {rs.name: self._rule_files(rs.name, rs.path) for rs in self.config.rules}
        key = self._cache_key()
        if not self._load_cache(key):
            self._compile()
            self._save_cache(key)
        if self._rules is None:
            raise EngineError("yara: no rules")
        self._scanner = yara_x.Scanner(self._rules)
        self._scanner.set_timeout(max(1, round(self.timeout)))
        for name in EXTERNALS:
            self._scanner.set_global(name, "")
        if self.excluded_rules:
            log_event(
                logger,
                "yara_rules_excluded",
                logging.WARNING,
                count=len(self.excluded_rules),
                errors=self.excluded_rules,
            )
        log_event(logger, "yara_loaded", rules=self.rule_count)

    def _rule_files(self, name: str, path: Path) -> list[Path]:
        if path.is_file():
            files = [path]
        elif path.is_dir():
            files = sorted(
                p for p in path.rglob("*") if p.suffix.lower() in RULE_SUFFIXES and p.is_file()
            )
        else:
            raise EngineError(f"yara: rule set {name}: not found: {path}")
        files = [f for f in files if not _excluded(f, self.config.exclude)]
        if not files:
            raise EngineError(f"yara: rule set {name}: no rule file in {path}")
        return files

    def _cache_key(self) -> str:
        digest = hashlib.sha256()
        options = {
            "format": _CACHE_FORMAT,
            "yara-x": importlib.metadata.version("yara-x"),
            "externals": EXTERNALS,
            "on_compile_error": self.config.on_compile_error,
        }
        digest.update(json.dumps(options, sort_keys=True).encode())
        for name, files in self._files.items():
            for path in files:
                digest.update(f"\0{name}\0{path}\0".encode())
                try:
                    digest.update(hashlib.sha256(path.read_bytes()).digest())
                except OSError as ex:
                    raise EngineError(f"yara: cannot read {path}: {ex.strerror}") from ex
        return digest.hexdigest()

    def _compile(self) -> None:
        compiler = yara_x.Compiler()
        # Collect every error instead of stopping at the first one
        compiler.ignore_invalid_rules(True)
        for name in EXTERNALS:
            compiler.define_global(name, "")
        for set_name, files in self._files.items():
            root = next(rs.path for rs in self.config.rules if rs.name == set_name)
            for path in files:
                relative = path.relative_to(root) if path != root else Path(path.name)
                compiler.new_namespace(f"{set_name}/{relative}")
                try:
                    source = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as ex:
                    raise EngineError(f"yara: cannot read {path}: {ex}") from ex
                # Errors are recorded in compiler.errors()
                with contextlib.suppress(yara_x.CompileError):
                    compiler.add_source(source, origin=str(path))
        errors = [_describe_error(e) for e in compiler.errors()]
        if errors and self.config.on_compile_error == "fail":
            listed = "\n  ".join(errors[:_MAX_LISTED_ERRORS])
            more = len(errors) - _MAX_LISTED_ERRORS
            suffix = f"\n  ... and {more} more" if more > 0 else ""
            raise EngineError(
                f"yara: {len(errors)} compile errors (engines.yara.on_compile_error = "
                f'"fail"):\n  {listed}{suffix}'
            )
        self.excluded_rules = errors
        self._rules = compiler.build()
        self.rule_count = sum(1 for _ in self._rules)
        if self.rule_count == 0:
            raise EngineError("yara: no rule compiled")

    def _cache_paths(self, key: str) -> tuple[Path, Path] | None:
        if self.config.cache_dir is None:
            return None
        return self.config.cache_dir / f"{key}.rules", self.config.cache_dir / f"{key}.json"

    def _load_cache(self, key: str) -> bool:
        paths = self._cache_paths(key)
        if paths is None:
            return False
        rules_path, meta_path = paths
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            with rules_path.open("rb") as f:
                self._rules = yara_x.Rules.deserialize_from(f)
        except FileNotFoundError:
            return False
        except Exception as ex:  # a damaged cache is rebuilt
            log_event(logger, "yara_cache_invalid", logging.WARNING, error=str(ex))
            return False
        self.rule_count = int(meta["rule_count"])
        self.excluded_rules = [str(e) for e in meta["excluded_rules"]]
        return True

    def _save_cache(self, key: str) -> None:
        paths = self._cache_paths(key)
        if paths is None or self._rules is None:
            return
        rules_path, meta_path = paths
        meta = {"rule_count": self.rule_count, "excluded_rules": self.excluded_rules}
        try:
            rules_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            buffer = io.BytesIO()
            self._rules.serialize_into(buffer)
            _write_private(rules_path, buffer.getvalue())
            _write_private(meta_path, json.dumps(meta).encode())
        except OSError as ex:
            log_event(logger, "yara_cache_not_saved", logging.WARNING, error=ex.strerror)

    # -- information -----------------------------------------------------------

    def version(self) -> str:
        return f"YARA-X {importlib.metadata.version('yara-x')}"

    def signature_info(self) -> list[SignatureInfo]:
        return [describe_file(rs.path, rs.name) for rs in self.config.rules]

    def extra_info(self) -> dict[str, Any]:
        return {"rule_count": self.rule_count, "excluded_rules": self.excluded_rules}

    # -- scan --------------------------------------------------------------------

    def scan(self, file: FileInfo) -> EngineResult:
        if self._scanner is None:
            raise EngineError("yara: not loaded")
        for name, value in externals(file).items():
            self._scanner.set_global(name, value)
        try:
            # Read through the descriptor: opening /proc/self/fd/N would open
            # the file again, with the permissions of the sandboxed worker
            # (refused for a file private to its owner on ext4). yara-x only
            # scans bytes; the size is bounded by limits.max_file_size.
            data = _read_all(file.fd, file.size)
        except OSError as ex:
            return EngineResult(self.name, Verdict.ERROR, error=f"read error: {ex.strerror}")
        try:
            results = self._scanner.scan(data)
        except yara_x.TimeoutError:
            return EngineResult(self.name, Verdict.ERROR, error=f"timeout ({self.timeout:g}s)")
        except yara_x.ScanError as ex:
            return EngineResult(self.name, Verdict.ERROR, error=f"scan error: {ex}")
        malicious: list[str] = []
        suspicious: list[str] = []
        informational: list[str] = []
        for rule in results.matching_rules:
            name = f"{rule.namespace.split('/')[0]}:{rule.identifier}"
            score = _score(rule.metadata, self.config.default_score)
            if score >= self.config.malicious_score:
                malicious.append(name)
            elif score >= self.config.suspicious_score:
                suspicious.append(name)
            else:
                informational.append(name)
        facts: dict[str, str | bool | int | float] = {}
        if informational:
            facts["informational"] = ", ".join(informational)
        if malicious:
            return EngineResult(
                self.name, Verdict.MALICIOUS, tuple(malicious + suspicious), facts=facts
            )
        if suspicious:
            return EngineResult(self.name, Verdict.SUSPICIOUS, tuple(suspicious), facts=facts)
        return EngineResult(self.name, Verdict.CLEAN, facts=facts)


def _read_all(fd: int, size: int) -> bytes:
    """Read a file from its start, without moving the offset of the descriptor.

    One read for a regular file (a single chunk is joined without a copy).
    """
    chunks = []
    offset = 0
    while offset < size:
        chunk = os.pread(fd, size - offset, offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def externals(file: FileInfo) -> dict[str, str]:
    """External variables of the signature-base rules for one file."""
    name = file.name
    _, extension = posixpath.splitext(name)
    return {
        "filename": name,
        "filepath": "/" + file.rel_path,
        "extension": extension.lower(),
        "filetype": FILETYPES.get(file.mime or "", ""),
        "owner": "",
    }


def _score(metadata: tuple[tuple[str, Any], ...], default: int) -> int:
    for key, value in metadata:
        if key != "score":
            continue
        if isinstance(value, bool):
            break
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        break
    return default


def _describe_error(error: dict[str, Any]) -> str:
    labels = error.get("labels") or [{}]
    origin = labels[0].get("code_origin") or "?"
    line = labels[0].get("line") or error.get("line") or "?"
    return f"{origin}:{line}: {error.get('code', '')} {error.get('title', '')}".strip()


def _excluded(path: Path, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(str(path), pattern) for pattern in patterns)


def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    tmp.replace(path)
