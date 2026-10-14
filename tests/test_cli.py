from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from pandora_box.cli import apply_overrides, main, parse_args, stop_on_sigterm
from pandora_box.config import parse_config

# Sandboxed scan workers need root (see test_sandbox_needs_root)
NO_SANDBOX = "[scan]\nsandbox = false\n"


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "pandora-box.toml"
    path.write_text(content)
    return path


def test_check_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path, "[kiosk]\nfake_scan = true\n" + NO_SANDBOX)
    assert main(["--config", str(path), "--check-config"]) == 0
    assert "OK" in capsys.readouterr().out


@pytest.mark.skipif(os.geteuid() == 0, reason="checks the error of a user without root")
def test_sandbox_needs_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path, "[kiosk]\nfake_scan = true\n")
    assert main(["--config", str(path), "--check-config"]) == 2
    assert "scan sandbox needs root (set scan.sandbox = false" in capsys.readouterr().err


def test_invalid_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path, "[kiosk]\nfake_scan = 1\n")
    assert main(["--config", str(path), "--check-config"]) == 2
    assert "kiosk.fake_scan" in capsys.readouterr().err


def test_refuses_to_start_without_engine_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Every engine is enabled by default: without a signature set, or without
    # engine data, the configuration is refused
    folder = tmp_path / "signatures"
    path = write(tmp_path, NO_SANDBOX + f'[signatures]\nfolder = "{folder}"\n')
    assert main(["--config", str(path), "--check-config"]) == 2
    assert f"signatures: no signature set installed in {folder}" in capsys.readouterr().err
    assert main(["--config", str(path), "--check-config", "--fake-scan"]) == 0
    path = write(tmp_path, NO_SANDBOX + "[signatures]\nverify = false\n")
    assert main(["--config", str(path), "--check-config"]) == 2
    assert "cannot load an enabled engine: malwarebazaar" in capsys.readouterr().err


def test_refuses_to_start_without_content_engine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(
        tmp_path,
        "[engines.clamav]\nenabled = false\n[engines.yara]\nenabled = false\n",
    )
    assert main(["--config", str(path), "--check-config"]) == 2
    assert "no content engine" in capsys.readouterr().err


def test_overrides() -> None:
    args = parse_args(["--fake-scan", "--interface", "console"])
    config = apply_overrides(parse_config({}), args)
    assert config.kiosk.fake_scan is True
    assert config.kiosk.interface == "console"


def test_sigterm_stops_like_ctrl_c() -> None:
    # systemctl stop: the kiosk unmounts the device and exits 0, instead of
    # the exit(1) of the ncurses handler
    previous = signal.getsignal(signal.SIGTERM)
    try:
        stop_on_sigterm()
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, previous)
