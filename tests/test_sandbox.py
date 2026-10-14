from __future__ import annotations

import pwd
import subprocess
import sys
from pathlib import Path

import pytest

from pandora_box.config import parse_config
from pandora_box.device import mount_options
from pandora_box.kiosk import build_sandbox
from pandora_box.sandbox import Sandbox, SandboxError


@pytest.fixture
def scan_user(monkeypatch: pytest.MonkeyPatch) -> None:
    def getpwnam(name: str) -> pwd.struct_passwd:
        if name != "pandora-box-scan":
            raise KeyError(name)
        return pwd.struct_passwd((name, "x", 990, 990, "", "/", "/usr/sbin/nologin"))

    monkeypatch.setattr(pwd, "getpwnam", getpwnam)


def test_wrap(scan_user: None) -> None:
    yara = Path("/var/lib/pandora-box-signatures/current/yara")
    sandbox = Sandbox("pandora-box-scan", (yara,), (Path("/cache"),))
    argv = sandbox.wrap(["python3", "-m", "pandora_box.worker", "3"])
    assert argv[0] == "bwrap"
    for option in ("--unshare-net", "--unshare-pid", "--unshare-ipc", "--die-with-parent"):
        assert option in argv
    # No user namespace: bwrap runs as root, user namespaces stay disabled
    assert "--unshare-user" not in argv
    assert "--unshare-all" not in argv
    text = " ".join(argv)
    assert "--ro-bind-try /var/lib/pandora-box-signatures/current/yara " in text
    assert "--bind /cache /cache" in text
    assert "/media" not in text
    setpriv = argv.index("setpriv")
    assert argv[setpriv:] == [
        "setpriv",
        "--reuid=990",
        "--regid=990",
        "--clear-groups",
        "--inh-caps=-all",
        "--bounding-set=-all",
        "--no-new-privs",
        "--",
        "python3",
        "-m",
        "pandora_box.worker",
        "3",
    ]


def test_unknown_user() -> None:
    with pytest.raises(SandboxError, match="no user"):
        Sandbox("no-such-user-pandora-box").ids()


def test_root_user_is_refused() -> None:
    with pytest.raises(SandboxError, match="must not be root"):
        Sandbox("root").ids()


def test_build_sandbox(scan_user: None, tmp_path: Path) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    config = parse_config(
        {
            "engines": {
                "malwarebazaar": {"database": "/sig/mb/db.bin"},
                "hashlookup": {"enabled": False},
                "clamav": {"socket": "/run/clamav/clamd.ctl"},
                "yara": {
                    "rules": [{"name": "r", "path": str(rules)}],
                    "cache_dir": "/var/cache/yara",
                },
            }
        }
    )
    sandbox = build_sandbox(config)
    assert sandbox is not None
    assert sandbox.read_only == (Path("/run/clamav"), Path("/sig/mb"), rules)
    assert sandbox.writable == (Path("/var/cache/yara"),)
    assert build_sandbox(parse_config({"scan": {"sandbox": False}})) is None


def test_mount_options_readable_by_the_workers() -> None:
    options = mount_options("vfat", read_only=True, uid=0, gid=0, reader_gid=990)
    assert "gid=990" in options
    assert "fmask=0137" in options
    assert "dmask=0027" in options


SECCOMP_CHECK = """
import os, socket, threading
from pandora_box.seccomp import apply_filter
apply_filter()
results = []
t = threading.Thread(target=lambda: results.append("thread"))
t.start()
t.join()
socket.socket(socket.AF_UNIX).close()
for name, call in (
    ("inet", lambda: socket.socket(socket.AF_INET)),
    ("execve", lambda: os.execv("/bin/true", ["true"])),
):
    try:
        call()
    except OSError:
        results.append(name + " refused")
status = open("/proc/self/status").read()
results.append("nnp" if "NoNewPrivs:\\t1" in status else "privs")
print(" ".join(results))
"""


def test_seccomp_filter() -> None:
    try:
        from pandora_box.seccomp import SeccompError, apply_filter  # noqa: F401
    except ImportError:
        pytest.skip("no seccomp module")
    result = subprocess.run(
        [sys.executable, "-c", SECCOMP_CHECK], capture_output=True, text=True, check=False
    )
    if "libseccomp not available" in result.stderr:
        pytest.skip("libseccomp not installed")
    assert result.stdout.split() == ["thread", "inet", "refused", "execve", "refused", "nnp"]
