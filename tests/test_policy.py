from __future__ import annotations

from pathlib import Path

import pytest

from pandora_box.engines import EngineResult, Verdict
from pandora_box.policy import KNOWN_FILE, DeviceVerdict, aggregate_file, device_verdict
from pandora_box.results import FileResult, ScanSummary

CONTENT = ("clamav", "yara")
C, S, M, E, K = Verdict.CLEAN, Verdict.SUSPICIOUS, Verdict.MALICIOUS, Verdict.ERROR, Verdict.SKIPPED


def results(**verdicts: Verdict) -> list[EngineResult]:
    return [
        EngineResult(name, v, reason=KNOWN_FILE if v is K else None) for name, v in verdicts.items()
    ]


@pytest.mark.parametrize(
    ("engines", "min_malicious", "expected"),
    [
        # every engine clean
        ({"malwarebazaar": C, "clamav": C, "yara": C}, 1, C),
        # one positive engine is enough
        ({"malwarebazaar": C, "clamav": M, "yara": C}, 1, M),
        ({"malwarebazaar": M, "clamav": C, "yara": C}, 1, M),
        ({"clamav": C, "yara": S}, 1, S),
        ({"clamav": M, "yara": S}, 1, M),
        # a detection wins over an error
        ({"clamav": E, "yara": M}, 1, M),
        ({"clamav": E, "yara": S}, 1, S),
        # an error is never clean
        ({"clamav": E, "yara": C}, 1, E),
        ({"malwarebazaar": E, "clamav": C, "yara": C}, 1, E),
        # a missing content engine result is an error
        ({"malwarebazaar": C, "clamav": C}, 1, E),
        ({"malwarebazaar": C}, 1, E),
        ({}, 1, E),
        # content engines skipped for a known file
        ({"hashlookup": C, "clamav": K, "yara": K}, 1, C),
        # two engines must agree
        ({"clamav": M, "yara": C}, 2, S),
        ({"clamav": M, "yara": M}, 2, M),
    ],
)
def test_aggregate_file(engines: dict[str, Verdict], min_malicious: int, expected: Verdict) -> None:
    assert aggregate_file(results(**engines), CONTENT, min_malicious) is expected


def test_skipped_for_another_reason_is_an_error() -> None:
    skipped = [EngineResult("clamav", K, reason="disabled"), EngineResult("yara", C)]
    assert aggregate_file(skipped, CONTENT) is E


def test_no_content_engine_is_an_error() -> None:
    assert aggregate_file(results(malwarebazaar=C), ()) is E


def file(verdict: Verdict, incomplete: bool = False) -> FileResult:
    return FileResult(Path("/media/f"), 1, verdict, incomplete=incomplete)


@pytest.mark.parametrize(
    ("verdicts", "reasons", "expected"),
    [
        ([C, C], [], DeviceVerdict.CLEAN),
        ([], [], DeviceVerdict.CLEAN),
        ([C, M, E, S], [], DeviceVerdict.MALICIOUS),
        ([C, S, E], [], DeviceVerdict.SUSPICIOUS),
        ([C, E], [], DeviceVerdict.NOT_VERIFIED),
        ([C, K], [], DeviceVerdict.CLEAN),
        ([C], ["more than 10 files"], DeviceVerdict.NOT_VERIFIED),
    ],
)
def test_device_verdict(
    verdicts: list[Verdict], reasons: list[str], expected: DeviceVerdict
) -> None:
    summary = ScanSummary([file(v) for v in verdicts], incomplete_reasons=reasons)
    assert device_verdict(summary) is expected


def test_skipped_over_limit_is_not_verified() -> None:
    summary = ScanSummary([file(C), file(K, incomplete=True)])
    assert device_verdict(summary) is DeviceVerdict.NOT_VERIFIED
