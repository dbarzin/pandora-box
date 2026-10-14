"""Detection regression corpus: see tests/corpus/README.md."""

from __future__ import annotations

import io
import os
import shutil
import tomllib
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pandora_box.config import LimitsConfig, load_config
from pandora_box.engines import Verdict
from pandora_box.kiosk import build_pool
from pandora_box.scanner import Scanner
from pandora_box.workers import WorkerPool

from .samples import eicar

CORPUS = Path(__file__).parent / "corpus"
CATEGORIES = ("harmless", "malicious", "false_positive")


def _eicar_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("eicar.com", eicar())
    return buffer.getvalue()


GENERATORS: dict[str, Callable[[], bytes]] = {
    "eicar": eicar,
    "eicar_zip": _eicar_zip,
    "hostile_name": lambda: b"harmless content\n",
}


def load_samples() -> list[dict[str, Any]]:
    with (CORPUS / "manifest.toml").open("rb") as f:
        samples: list[dict[str, Any]] = tomllib.load(f)["sample"]
    return samples


def device_path(sample: dict[str, Any]) -> str:
    return str(sample.get("path") or Path(sample["file"]).name)


def build_device(root: Path) -> None:
    for sample in load_samples():
        target = root / device_path(sample)
        target.parent.mkdir(parents=True, exist_ok=True)
        if "file" in sample:
            shutil.copyfile(CORPUS / sample["file"], target)
        else:
            target.write_bytes(GENERATORS[sample["generator"]]())


def check_corpus(pool: WorkerPool, root: Path) -> None:
    build_device(root)
    summary = Scanner(pool, LimitsConfig()).scan_tree(root)
    verdicts = {f.rel_path: f for f in summary.files}
    engines = {e.name for e in pool.engines}
    failures = []
    for sample in load_samples():
        if "detected_by" in sample and not engines & set(sample["detected_by"]):
            continue
        result = verdicts[device_path(sample)]
        if result.verdict.value != sample["expected"]:
            detections = {r.engine: r.detail for r in result.results if r.detail}
            failures.append(
                f"{sample['name']}: expected {sample['expected']}, got {result.verdict.value} "
                f"{detections or result.detail}"
            )
    assert not failures, "\n".join(failures)


def test_manifest_is_valid() -> None:
    names = set()
    for sample in load_samples():
        assert sample["name"] not in names, f"duplicate sample {sample['name']}"
        names.add(sample["name"])
        assert sample["category"] in CATEGORIES, sample["name"]
        assert sample["expected"] in {v.value for v in Verdict}, sample["name"]
        assert sample.get("reason"), f"{sample['name']}: a reason is required"
        assert ("file" in sample) != ("generator" in sample), sample["name"]
        if "file" in sample:
            assert (CORPUS / sample["file"]).is_file(), sample["file"]
        else:
            assert sample["generator"] in GENERATORS, sample["generator"]
        if "detected_by" in sample:
            assert sample["detected_by"] and sample["expected"] != "clean", sample["name"]
        if sample["category"] == "false_positive":
            assert sample["expected"] == "clean", sample["name"]


def test_corpus_with_fake_engine(tmp_path: Path, fake_pool: WorkerPool) -> None:
    check_corpus(fake_pool, tmp_path / "device")


CORPUS_CONFIG = os.environ.get("PANDORA_BOX_CORPUS_CONFIG")


@pytest.fixture
def real_pool() -> Iterator[WorkerPool]:
    assert CORPUS_CONFIG
    with build_pool(load_config(Path(CORPUS_CONFIG))) as pool:
        yield pool


@pytest.mark.integration
@pytest.mark.skipif(not CORPUS_CONFIG, reason="PANDORA_BOX_CORPUS_CONFIG not set")
def test_corpus_with_real_engines(tmp_path: Path, real_pool: WorkerPool) -> None:
    check_corpus(real_pool, tmp_path / "device")
