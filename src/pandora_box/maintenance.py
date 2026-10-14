"""Maintenance devices: the kiosk exports its logs to a signed USB key.

A kiosk has no login and no network access from outside: to diagnose it, the
operator inserts a maintenance device, which holds at its root:

    pandora-box-maintenance/
        manifest.json, manifest.json.sig   signed with the update key,
                                           content "maintenance"
        request/request.json               {"action": "export-logs", "kiosk": ""}

(pandora-box-signatures maintenance FOLDER --key KEY). The kiosk verifies
the signature, that the request is recent (VALID_DAYS: an old key cannot be
used again) and meant for it ("kiosk": its name, or "" for any), then
writes its logs to pandora-box-maintenance/<kiosk>-<date>/ and ejects the
device, which is not scanned. Nothing changes on the kiosk. The export holds
the journal, the logs of the kiosk and of USBGuard, the state of the
services and of the hardware; never a key, a credential or a scan report.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pandora_box import sigsets
from pandora_box.config import Config

FOLDER = "pandora-box-maintenance"
# A file of a set is always in a folder
REQUEST = "request/request.json"
CONTENT_MAINTENANCE = "maintenance"
EXPORT_LOGS = "export-logs"
VALID_DAYS = 7
# Each exported file is truncated beyond this size
MAX_OUTPUT = 32 * 1024 * 1024

JOURNALCTL = "/usr/bin/journalctl"
SYSTEMCTL = "/usr/bin/systemctl"
USBGUARD = "/usr/bin/usbguard"
LSBLK = "/usr/bin/lsblk"
BOOTCTL = "/usr/bin/bootctl"

# Exported file -> command (absolute paths: never looked up in PATH)
COMMANDS: tuple[tuple[str, Sequence[str]], ...] = (
    ("journal.txt", (JOURNALCTL, "-b", "0", "-o", "short-monotonic", "--no-pager")),
    ("journal-previous-boot.txt",
     (JOURNALCTL, "-b", "-1", "-o", "short-monotonic", "--no-pager")),
    ("boots.txt", (JOURNALCTL, "--list-boots", "--no-pager")),
    ("kernel.txt", (JOURNALCTL, "-k", "-b", "0", "-o", "short-monotonic", "--no-pager")),
    ("services.txt", (SYSTEMCTL, "list-units", "--all", "--no-pager")),
    ("failed-services.txt", (SYSTEMCTL, "--failed", "--no-pager")),
    ("usb-devices.txt", (USBGUARD, "list-devices")),
    ("usb-rules.txt", (USBGUARD, "list-rules")),
    ("disks.txt", (LSBLK, "-o", "NAME,SIZE,TYPE,FSTYPE,PARTLABEL,MOUNTPOINTS")),
    ("boot-entries.txt", (BOOTCTL, "list", "--no-pager")),
)  # fmt: skip
# Exported file -> file of the kiosk
FILES: tuple[tuple[str, str], ...] = (
    ("pandora-box.log", "/var/log/pandora-box/pandora-box.log"),
    ("usbguard-audit.log", "/var/log/usbguard/usbguard-audit.log"),
    ("os-release.txt", "/etc/os-release"),
    ("cmdline.txt", "/proc/cmdline"),
    ("cpuinfo.txt", "/proc/cpuinfo"),
    ("meminfo.txt", "/proc/meminfo"),
    ("input-devices.txt", "/proc/bus/input/devices"),
)


class MaintenanceError(Exception):
    pass


def verify(folder: Path, config: Config, now: datetime | None = None) -> sigsets.Manifest:
    """Verify the maintenance request of a device; return its manifest."""
    keys = sigsets.trusted_keys(config.signatures.keys)
    try:
        manifest, _, _ = sigsets.read_set(folder, keys, CONTENT_MAINTENANCE)
    except sigsets.SignatureSetError as ex:
        raise MaintenanceError(str(ex)) from ex
    entry = manifest.entry(REQUEST)
    if entry is None or entry.size > 4096:
        raise MaintenanceError(f"no {REQUEST} in the request")
    data = (folder / REQUEST).read_bytes()[: entry.size + 1]
    if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
        raise MaintenanceError(f"{REQUEST} does not match the signed request")
    try:
        request = json.loads(data)
    except ValueError as ex:
        raise MaintenanceError(f"invalid {REQUEST}") from ex
    if not isinstance(request, dict) or request.get("action") != EXPORT_LOGS:
        raise MaintenanceError("unknown maintenance action")
    kiosk = request.get("kiosk", "")
    if kiosk not in ("", config.kiosk.name):
        raise MaintenanceError(f"request for the kiosk {str(kiosk)[:64]!r}")
    now = now or datetime.now(UTC)
    age = now - manifest.created
    # A clock a little ahead on the computer that signed is accepted
    if age > timedelta(days=VALID_DAYS) or age < -timedelta(days=1):
        raise MaintenanceError(
            f"request of {manifest.created:%Y-%m-%d}: valid {VALID_DAYS} days, sign a new one"
        )
    return manifest


def export(
    folder: Path,
    config: Config,
    now: datetime | None = None,
    run: Callable[[Sequence[str]], bytes] | None = None,
    commands: Sequence[tuple[str, Sequence[str]]] | None = None,
    files: Sequence[tuple[str, str]] | None = None,
) -> tuple[Path, int]:
    """Write the logs of the kiosk below folder; return the export folder, files written."""
    commands = COMMANDS if commands is None else commands
    files = FILES if files is None else files
    now = now or datetime.now(UTC)
    run = run or _run
    name = "".join(c if c.isalnum() or c in "-_." else "_" for c in config.kiosk.name)
    target = folder / f"{name or 'kiosk'}-{now:%Y%m%d-%H%M%S}"
    target.mkdir()
    written = 0
    for filename, argv in commands:
        if not Path(argv[0]).exists():
            continue
        _write(target / filename, run(argv))
        written += 1
    for filename, source in files:
        try:
            with Path(source).open("rb") as f:
                data = f.read(MAX_OUTPUT + 1)
        except OSError as ex:
            data = f"{source}: {ex.strerror}\n".encode()
        _write(target / filename, data)
        written += 1
    _write(target / "hardware.txt", hardware().encode())
    _write(target / "signatures.txt", signature_state(config).encode())
    os.sync()
    return target, written + 2


def hardware() -> str:
    """Model of the computer, its screens and framebuffer (sysfs)."""
    lines = []
    for name in ("sys_vendor", "product_name", "product_version", "board_name", "bios_version",
                 "bios_date"):  # fmt: skip
        lines.append(f"{name}: {_read(Path('/sys/class/dmi/id') / name)}")
    for connector in sorted(Path("/sys/class/drm").glob("card*-*")):
        modes = _read(connector / "modes").replace("\n", " ")
        lines.append(f"{connector.name}: {_read(connector / 'status')}, modes: {modes}")
    for fb in sorted(Path("/sys/class/graphics").glob("fb*")):
        lines.append(f"{fb.name}: {_read(fb / 'name')} {_read(fb / 'virtual_size')}")
    return "\n".join(lines) + "\n"


def signature_state(config: Config) -> str:
    folder = config.signatures.folder
    manifest = sigsets.installed_manifest(folder)
    if manifest is None:
        return f"no signature set installed in {folder}\n"
    lines = [f"set {manifest.serial}, created {manifest.created.isoformat()}"]
    lines += [f"{f.path}: {f.size} bytes, {f.version} {f.date}" for f in manifest.files]
    return "\n".join(lines) + "\n"


def request(folder: Path, key: Path, kiosk: str = "") -> sigsets.Manifest:
    """Write a signed maintenance request into folder (pandora-box-maintenance)."""
    (folder / REQUEST).parent.mkdir(parents=True, exist_ok=True)
    (folder / REQUEST).write_text(json.dumps({"action": EXPORT_LOGS, "kiosk": kiosk}) + "\n")
    for name in (sigsets.MANIFEST, sigsets.SIGNATURE):
        (folder / name).unlink(missing_ok=True)
    # Only the request is listed: the exports written next to it are not
    manifest = sigsets.build(folder, content=CONTENT_MAINTENANCE, only=[REQUEST])
    sigsets.sign(folder, key)
    return manifest


def _run(argv: Sequence[str]) -> bytes:
    try:
        result = subprocess.run(  # noqa: S603  (fixed commands, no shell)
            list(argv), capture_output=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as ex:
        return f"{' '.join(argv)}: {ex}\n".encode()
    output = result.stdout
    if result.returncode != 0:
        output += f"\n[exit status {result.returncode}] ".encode() + result.stderr[-4096:]
    return output


def _write(path: Path, data: bytes) -> None:
    if len(data) > MAX_OUTPUT:
        data = data[:MAX_OUTPUT] + b"\n[truncated]\n"
    path.write_bytes(data)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return "?"
