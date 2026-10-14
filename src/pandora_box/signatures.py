"""Signature databases: provenance and freshness.

The origin and integrity of the signature files are verified by the kiosk
before the engines load them (pandora_box.sigsets). Their version, date and
source come from:

- the manifest.json of their folder, written by pandora_box.publish:

    {"hashlookup-full.bloom": {"version": "...", "date": "2026-10-01T00:00:00Z",
                               "source": "https://...", "sha256": "..."}}

- or else the manifest of the signed signature set holding them (the date
  of a file defaults to the creation date of the set);
- or else the file modification time, for the date.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pandora_box.engines.base import SignatureInfo

MANIFEST = "manifest.json"
# Manifest of a signed signature set (pandora_box.sigsets)
SET_FORMAT = "pandora-box-signatures"
MAX_DEPTH = 6


def describe_file(
    path: Path, source: str = "", version: str = "", date: datetime | None = None
) -> SignatureInfo:
    """Describe a signature file from its manifest entry.

    Without a manifest date, use the given date, or else the file mtime.
    """
    default_date = date
    entry = _manifest_entry(path)
    set_entry, created = _set_entry(path)
    entry = {**set_entry, **entry}
    if default_date is None and created is not None:
        default_date = created
    date = None
    raw_date = entry.get("date")
    if isinstance(raw_date, str):
        try:
            date = datetime.fromisoformat(raw_date)
        except ValueError:
            date = None
        if date is not None and date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
    if date is None:
        date = default_date
    if date is None:
        try:
            date = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        except OSError:
            date = None
    return SignatureInfo(
        name=path.name,
        version=str(entry.get("version") or version),
        date=date,
        path=str(path),
        source=str(entry.get("source") or source),
    )


def _manifest_entry(path: Path) -> dict[str, object]:
    manifest = path.parent / MANIFEST
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entry = data.get(path.name) if isinstance(data, dict) else None
    return entry if isinstance(entry, dict) else {}


def _set_entry(path: Path) -> tuple[dict[str, object], datetime | None]:
    """The entry of a file in the manifest of its signature set, and the set date."""
    for folder in list(path.parents)[:MAX_DEPTH]:
        try:
            data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("format") != SET_FORMAT:
            continue
        files = data.get("files")
        entry = files.get(path.relative_to(folder).as_posix()) if isinstance(files, dict) else None
        try:
            created = datetime.fromisoformat(str(data.get("created")))
        except ValueError:
            created = None
        return (entry if isinstance(entry, dict) else {}), created
    return {}, None


@dataclass(frozen=True)
class StaleSignature:
    engine: str
    signature: SignatureInfo
    age_days: float


def find_stale(
    engine: str, infos: Sequence[SignatureInfo], max_age_days: float, now: datetime | None = None
) -> list[StaleSignature]:
    """Signatures older than max_age_days, or without a known date."""
    now = now or datetime.now(UTC)
    stale = []
    for info in infos:
        if info.date is None:
            stale.append(StaleSignature(engine, info, float("inf")))
            continue
        age = (now - info.date).total_seconds() / 86400
        if age > max_age_days:
            stale.append(StaleSignature(engine, info, age))
    return stale
