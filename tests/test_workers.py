from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from pandora_box.engines import EngineError, EngineKind, EngineSpec, FakeEngine, Verdict
from pandora_box.inventory import take_inventory
from pandora_box.pipeline import PipelineOptions
from pandora_box.results import FileResult
from pandora_box.workers import WorkerPool, auto_workers

from .conftest import FAKE, started_pool
from .engines import CompromisedEngine, FailingLoadEngine, MisbehavingEngine, ProbeHashEngine


def make_files(root: Path, names: list[str]) -> None:
    for name in names:
        (root / name).write_text(name)


def run(pool: WorkerPool, root: Path) -> dict[str, FileResult]:
    entries = take_inventory(root, 1000, 10, 10**6).files
    results: list[FileResult] = []
    pool.scan(root, entries, results.append)
    assert len(results) == len(entries)
    return {r.rel_path: r for r in results}


def misbehaving(behavior: str, timeout: float = 60.0) -> list[EngineSpec]:
    return [
        EngineSpec("fake", FakeEngine),
        EngineSpec("misbehaving", MisbehavingEngine, (behavior, "bad-", timeout)),
    ]


def test_start_reports_engines() -> None:
    with started_pool(FAKE) as pool:
        [engine] = pool.engines
        assert engine.name == "fake"
        assert engine.kind is EngineKind.CONTENT
        assert engine.version == "fake"
        assert len(pool.pids) == 2
        assert pool.engine_info()[0].name == "fake"


def test_load_failure_refuses_to_start() -> None:
    pool = WorkerPool([EngineSpec("failing", FailingLoadEngine)], PipelineOptions(), 1, 60.0)
    with pytest.raises(EngineError, match="failing: no signatures"):
        pool.start()


def test_engine_exception(tmp_path: Path) -> None:
    make_files(tmp_path, ["ok.txt", "bad-raise.txt"])
    with started_pool(misbehaving("raise")) as pool:
        results = run(pool, tmp_path)
    assert results["ok.txt"].verdict is Verdict.CLEAN
    bad = results["bad-raise.txt"]
    assert bad.verdict is Verdict.ERROR
    assert "RuntimeError: boom" in bad.detail


def test_invalid_result(tmp_path: Path) -> None:
    make_files(tmp_path, ["bad-result.txt"])
    with started_pool(misbehaving("bad")) as pool:
        results = run(pool, tmp_path)
    assert results["bad-result.txt"].verdict is Verdict.ERROR
    assert "invalid engine result" in results["bad-result.txt"].detail


def test_late_clean_answer_is_an_error(tmp_path: Path) -> None:
    make_files(tmp_path, ["bad-slow.txt"])
    # The engine answers after its timeout, before the watchdog grace delay
    with started_pool(misbehaving("slow", timeout=0.2), engine_grace=5) as pool:
        results = run(pool, tmp_path)
    assert results["bad-slow.txt"].verdict is Verdict.ERROR
    assert "timeout (0.2s) exceeded" in results["bad-slow.txt"].detail


def test_hung_engine_is_killed(tmp_path: Path) -> None:
    make_files(tmp_path, ["bad-hang.txt", "a.txt", "b.txt", "c.txt"])
    start = time.monotonic()
    with started_pool(misbehaving("hang", timeout=0.5), workers=2) as pool:
        pids = set(pool.pids)
        results = run(pool, tmp_path)
        assert pool.restarts == 1
        assert set(pool.pids) != pids
        # The replacement worker scans normally
        assert run(pool, tmp_path)["a.txt"].verdict is Verdict.CLEAN
    assert time.monotonic() - start < 30
    hung = results["bad-hang.txt"]
    assert hung.verdict is Verdict.ERROR
    assert hung.detail == "misbehaving: engine timeout"
    # Only the hung engine: the other one answered for the file
    assert {r.engine: r.verdict for r in hung.results} == {
        "fake": Verdict.CLEAN,
        "misbehaving": Verdict.ERROR,
    }
    assert all(results[n].verdict is Verdict.CLEAN for n in ("a.txt", "b.txt", "c.txt"))


def test_file_timeout(tmp_path: Path) -> None:
    make_files(tmp_path, ["bad-hang.txt"])
    with started_pool(misbehaving("hang"), workers=1, file_timeout=0.5) as pool:
        results = run(pool, tmp_path)
    assert results["bad-hang.txt"].verdict is Verdict.ERROR
    assert "file scan timeout (0.5s" in results["bad-hang.txt"].detail


def test_crashed_worker(tmp_path: Path) -> None:
    make_files(tmp_path, ["bad-crash.txt", "ok1.txt", "ok2.txt"])
    with started_pool(misbehaving("crash"), workers=1) as pool:
        results = run(pool, tmp_path)
        assert pool.restarts == 1
    crashed = results["bad-crash.txt"]
    assert crashed.verdict is Verdict.ERROR
    assert crashed.detail == "misbehaving: scan worker crashed (exit code 3)"
    assert results["ok1.txt"].verdict is Verdict.CLEAN
    assert results["ok2.txt"].verdict is Verdict.CLEAN


def test_every_worker_crashes(tmp_path: Path) -> None:
    make_files(tmp_path, [f"bad-{i}.txt" for i in range(6)])
    with started_pool(misbehaving("crash"), workers=2) as pool:
        results = run(pool, tmp_path)
    assert all(r.verdict is Verdict.ERROR for r in results.values())


@pytest.mark.parametrize("payload", ["clean", "pickle", "garbage"])
def test_compromised_worker(tmp_path: Path, payload: str) -> None:
    """Forged messages of a worker are never trusted, and never unpickled."""
    root = tmp_path / "key"
    root.mkdir()
    make_files(root, ["evil.txt"])
    proof = tmp_path / "proof"
    specs = [
        EngineSpec("fake", FakeEngine),
        EngineSpec("compromised", CompromisedEngine, (payload, str(proof))),
    ]
    with started_pool(specs, workers=1) as pool:
        results = run(pool, root)
        assert pool.restarts == 1
    result = results["evil.txt"]
    assert result.verdict is Verdict.ERROR
    assert result.detail.startswith("compromised: scan worker killed: invalid message")
    assert not proof.exists()


def test_a_compromised_engine_cannot_forge_another_one(tmp_path: Path) -> None:
    """A worker only reports its own engine: the detection of another one stays."""
    from .samples import eicar

    root = tmp_path / "key"
    root.mkdir()
    (root / "evil.com").write_bytes(eicar())
    specs = [
        EngineSpec("fake", FakeEngine),
        EngineSpec("compromised", CompromisedEngine, ("other",)),
    ]
    with started_pool(specs, workers=1) as pool:
        results = run(pool, root)
    result = results["evil.com"]
    engines = {r.engine: r for r in result.results}
    assert engines["fake"].verdict is Verdict.MALICIOUS
    assert engines["compromised"].verdict is Verdict.ERROR
    assert "result of another engine: fake" in (engines["compromised"].error or "")
    assert result.verdict is Verdict.MALICIOUS


def test_one_worker_per_engine(tmp_path: Path) -> None:
    make_files(tmp_path, ["a.txt"])
    with started_pool(misbehaving("raise"), workers=3) as pool:
        # A content engine has scan.workers workers each: 2 engines x 3
        assert len(pool.pids) == 6
        result = run(pool, tmp_path)["a.txt"]
    # The kiosk hashed the file itself
    assert result.info is not None
    assert result.info.sha256 == hashlib.sha256(b"a.txt").hexdigest()
    assert [r.engine for r in result.results] == ["fake", "misbehaving"]


def test_unsafe_file_is_not_passed_to_a_worker(tmp_path: Path) -> None:
    make_files(tmp_path, ["a.txt"])
    entries = take_inventory(tmp_path, 1000, 10, 10**6).files
    (tmp_path / "a.txt").unlink()
    (tmp_path / "a.txt").write_text("replaced since the inventory")
    results: list[FileResult] = []
    with started_pool(FAKE) as pool:
        pool.scan(tmp_path, entries, results.append)
    [result] = results
    assert result.verdict is Verdict.ERROR
    # Replaced (new inode) or, when the inode is reused, a new size
    assert result.detail.endswith("since the inventory")


@pytest.mark.parametrize("skip", [False, True])
def test_hash_engines_never_get_the_file(tmp_path: Path, skip: bool) -> None:
    make_files(tmp_path, ["a.txt"])
    specs = [EngineSpec("probe", ProbeHashEngine), EngineSpec("fake", FakeEngine)]
    options = PipelineOptions(skip_content_for_known=skip)
    with started_pool(specs, workers=2, options=options) as pool:
        # One worker for the hash engine, scan.workers for the content engine
        assert len(pool.pids) == 3
        result = run(pool, tmp_path)["a.txt"]
    engines = {r.engine: r for r in result.results}
    assert engines["probe"].facts == {"known": True, "got_file": False}
    # A known file: the content engines are skipped only when configured
    assert engines["fake"].verdict is (Verdict.SKIPPED if skip else Verdict.CLEAN)
    assert result.verdict is Verdict.CLEAN


@pytest.mark.parametrize(
    ("memory_mb", "cpus", "workers"),
    [
        (8 * 1024, 6, 6),  # ThinkCentre M720q: one per CPU
        (2 * 1024, 4, 2),  # 2 GB: as many as the memory allows
        (2 * 1024, 2, 2),  # the test virtual machine
        (1024, 4, 1),  # never none
        (64 * 1024, 64, 16),  # at most 16
    ],
)
def test_auto_workers(tmp_path: Path, memory_mb: int, cpus: int, workers: int) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {memory_mb * 1024} kB\nMemFree:  1 kB\n")
    assert auto_workers(meminfo, cpus) == workers
    assert auto_workers(tmp_path / "missing", cpus) == min(cpus, 2)
