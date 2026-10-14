"""Scan every file of a mounted device with the detection engines."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from pandora_box.config import Config, LimitsConfig
from pandora_box.engines import Verdict
from pandora_box.inventory import UNREADABLE, Skipped, take_inventory
from pandora_box.logs import get_logger, log_event
from pandora_box.pipeline import PipelineOptions
from pandora_box.results import FileResult, ScanSummary
from pandora_box.text import escape, human_size, printable
from pandora_box.workers import EngineInfo, WorkerPool

__all__ = [
    "FileResult",
    "ProgressCallback",
    "ScanSummary",
    "Scanner",
    "describe",
    "pipeline_options",
]

logger = get_logger("scanner")

# Called after each file with the file result, the number of files done and
# the total number of files
ProgressCallback = Callable[[FileResult, int, int], None]


class Scanner:
    """Inventory a directory tree, then scan its regular files in the worker pool."""

    def __init__(self, pool: WorkerPool, limits: LimitsConfig) -> None:
        self.pool = pool
        self.limits = limits

    @property
    def engines(self) -> list[EngineInfo]:
        return self.pool.engines

    def scan_tree(self, root: Path, on_progress: ProgressCallback | None = None) -> ScanSummary:
        """Scan all files under root.

        on_progress is called from the calling thread after each file.
        """
        start = time.monotonic()
        summary = ScanSummary()
        try:
            inventory = take_inventory(
                root, self.limits.max_files, self.limits.max_depth, self.limits.max_file_size
            )
        except OSError as ex:
            log_event(logger, "inventory_failed", logging.ERROR, error=ex.strerror)
            summary.incomplete_reasons.append(f"cannot read the device: {ex.strerror}")
            summary.duration = time.monotonic() - start
            return summary
        summary.incomplete_reasons.extend(inventory.incomplete_reasons)
        total = len(inventory.files) + len(inventory.skipped)
        log_event(
            logger,
            "inventory_done",
            files=len(inventory.files),
            skipped=len(inventory.skipped),
            bytes=inventory.total_bytes,
            truncated=inventory.truncated,
        )

        def collect(result: FileResult) -> None:
            summary.files.append(result)
            self._log_result(result)
            if on_progress is not None:
                on_progress(result, len(summary.files), total)

        for skipped in inventory.skipped:
            collect(_skipped_result(root, skipped))
        self.pool.scan(root, inventory.files, collect)

        summary.duration = time.monotonic() - start
        log_event(
            logger,
            "scan_done",
            duration=round(summary.duration, 1),
            files_scanned=len(summary.files),
            files_infected=len(summary.infected),
            files_suspicious=summary.count(Verdict.SUSPICIOUS),
            files_error=summary.count(Verdict.ERROR),
            files_skipped=summary.count(Verdict.SKIPPED),
            complete=summary.complete,
            incomplete_reasons=summary.incomplete_reasons,
        )
        return summary

    @staticmethod
    def _log_result(result: FileResult) -> None:
        log_event(
            logger,
            "file_scanned",
            path=escape(result.rel_path),
            size=result.size,
            verdict=result.verdict.value,
            detail=result.detail,
            # Verdict, detail and time spent in each engine (seconds), as
            # measured by its worker: the file duration also counts the waits
            engines={
                r.engine: [r.verdict.value, r.detail, round(r.duration, 3)] for r in result.results
            },
            duration=round(result.duration, 3),
        )


def _skipped_result(root: Path, skipped: Skipped) -> FileResult:
    # An unreadable entry is an error: it may hide anything
    verdict = Verdict.ERROR if skipped.reason.startswith(UNREADABLE) else Verdict.SKIPPED
    return FileResult(
        path=root / skipped.rel_path,
        size=skipped.size,
        verdict=verdict,
        detail=skipped.reason,
        rel_path=skipped.rel_path,
        incomplete=skipped.incomplete,
    )


def pipeline_options(config: Config) -> PipelineOptions:
    hashlookup = config.engines.hashlookup
    return PipelineOptions(
        skip_content_for_known=hashlookup.enabled and hashlookup.skip_content_engines,
        min_malicious_engines=config.policy.min_malicious_engines,
    )


def describe(result: FileResult) -> str:
    name = printable(result.path.name)
    return f"{name} [{human_size(result.size)}] -> {result.verdict.value.upper()}"
