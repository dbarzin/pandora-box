"""Verdict aggregation: per file, then per device. Fail closed by default.

File verdict, from the results of its engines:

1. malicious if at least policy.min_malicious_engines engines report it as
   malicious (default: one is enough);
2. otherwise suspicious if an engine reports it as malicious or suspicious;
3. otherwise error if an engine failed, or if a content engine gave no
   result, or if every content engine skipped it for a reason other than a
   known file (hashlookup);
4. otherwise clean.

A file is never clean without at least one content engine result, unless it
is a known file and the content engines were skipped on purpose.

Device verdict: the worst file verdict (malicious, then suspicious, then not
verified when a file or the device was not fully scanned, then clean). The
kiosk applies scan.suspicious and scan.on_error to it.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from enum import StrEnum

from pandora_box.engines import EngineResult, Verdict
from pandora_box.results import ScanSummary

# Skip reason of content engines on files known by a hash engine
KNOWN_FILE = "known file (hashlookup)"


def aggregate_file(
    results: Iterable[EngineResult], content_engines: Collection[str], min_malicious: int = 1
) -> Verdict:
    results = list(results)
    malicious = sum(1 for r in results if r.verdict is Verdict.MALICIOUS)
    if malicious and malicious >= min_malicious:
        return Verdict.MALICIOUS
    if malicious or any(r.verdict is Verdict.SUSPICIOUS for r in results):
        return Verdict.SUSPICIOUS
    if any(r.verdict is Verdict.ERROR for r in results):
        return Verdict.ERROR
    content = {r.engine: r for r in results if r.engine in content_engines}
    if not content_engines or set(content) != set(content_engines):
        return Verdict.ERROR
    for result in content.values():
        if result.verdict is Verdict.SKIPPED and result.reason != KNOWN_FILE:
            return Verdict.ERROR
    return Verdict.CLEAN


class DeviceVerdict(StrEnum):
    CLEAN = "clean"
    MALICIOUS = "malicious"
    SUSPICIOUS = "suspicious"
    # A file or a part of the device could not be fully scanned
    NOT_VERIFIED = "not_verified"


def device_verdict(summary: ScanSummary) -> DeviceVerdict:
    if summary.infected:
        return DeviceVerdict.MALICIOUS
    if summary.suspicious:
        return DeviceVerdict.SUSPICIOUS
    if not summary.complete:
        return DeviceVerdict.NOT_VERIFIED
    return DeviceVerdict.CLEAN
