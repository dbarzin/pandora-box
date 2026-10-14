from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path

import pytest

from pandora_box.filetype import FileTypeDetector, magic_version
from pandora_box.hashing import hash_fd
from pandora_box.lock import AlreadyRunningError, InstanceLock
from pandora_box.logs import get_logger, log_event, setup_logging
from pandora_box.statemachine import State, StateMachine
from pandora_box.text import escape, human_size, path_b64, printable


def test_human_size() -> None:
    assert human_size(512) == "512.0B"
    assert human_size(1536) == "1.5KB"
    assert human_size(1024**3) == "1.0GB"


def test_printable_strips_terminal_escapes() -> None:
    assert printable("evil\x1b[2Jname\n") == "evil?[2Jname?"
    assert printable("bad-\udcff.txt") == "bad-\\udcff.txt"
    assert printable(None) == ""


def test_state_machine() -> None:
    visited: list[State] = []

    def go(target: State) -> State:
        visited.append(target)
        return target

    machine = StateMachine(
        {State.START: lambda: go(State.WAIT), State.WAIT: lambda: go(State.STOP)}
    )
    machine.run()
    assert visited == [State.WAIT, State.STOP]


def test_unknown_state_stops() -> None:
    machine = StateMachine({State.START: lambda: State.SCAN})
    machine.run()
    assert machine.state is State.STOP


def test_lock() -> None:
    first = InstanceLock("pandora-box-test")
    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            InstanceLock("pandora-box-test").acquire()
    finally:
        first.release()
    second = InstanceLock("pandora-box-test")
    second.acquire()
    second.release()


def test_json_logs(tmp_path: Path) -> None:
    file = tmp_path / "logs" / "pandora-box.log"
    setup_logging("kiosk-01", "INFO", file)
    log_event(get_logger("test"), "file_scanned", path="bad-\udcff", size=3)
    log_event(get_logger("test"), "debug_only", logging.DEBUG)
    lines = file.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kiosk"] == "kiosk-01"
    assert entry["event"] == "file_scanned"
    assert entry["path"] == "bad-\udcff"
    assert entry["level"] == "INFO"
    setup_logging("kiosk-01", "INFO", None)


def test_escape() -> None:
    assert escape("docs/report.pdf") == "docs/report.pdf"
    assert escape(os.fsdecode(b"bad-\xff.txt")) == "bad-\\xff.txt"
    assert escape("esc\x1b[2J\nx") == "esc\\x1b[2J\\x0ax"
    assert escape("a\\b") == "a\\\\b"
    assert escape("rtl-‮.exe") == "rtl-\\u202e.exe"
    assert escape("tag-\U000e0041") == "tag-\\U000e0041"
    assert escape("été") == "été"
    escape(os.fsdecode(b"\xff")).encode("utf-8")  # always valid UTF-8


def test_path_b64() -> None:
    assert base64.b64decode(path_b64(os.fsdecode(b"bad-\xff"))) == b"bad-\xff"


def test_hash_fd(tmp_path: Path) -> None:
    data = os.urandom(3 * 1024 + 5)
    (tmp_path / "f").write_bytes(data)
    fd = os.open(tmp_path / "f", os.O_RDONLY)
    try:
        hashes = hash_fd(fd, chunk_size=1024)
    finally:
        os.close(fd)
    assert hashes.size == len(data)
    assert hashes.sha256 == hashlib.sha256(data).hexdigest()
    assert hashes.sha1 == hashlib.sha1(data).hexdigest()
    assert hashes.md5 == hashlib.md5(data).hexdigest()


def test_file_type(tmp_path: Path) -> None:
    (tmp_path / "f.pdf").write_bytes(b"%PDF-1.4\n")
    fd = os.open(tmp_path / "f.pdf", os.O_RDONLY)
    try:
        file_type = FileTypeDetector().identify(fd)
    finally:
        os.close(fd)
    assert file_type.mime == "application/pdf"
    assert file_type.description is not None and file_type.description.startswith("PDF")
    assert magic_version()
