"""The kiosk: wait for a device, scan it, then remove infected files."""

from __future__ import annotations

import logging
import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from usb_pasteur import __version__, imageupdate, maintenance
from usb_pasteur.config import Config
from usb_pasteur.device import DeviceError, Mounter, SystemMountWatcher, UsbDevice
from usb_pasteur.engines import Engine, EngineError, EngineKind, Verdict
from usb_pasteur.engines.registry import NoEngineError, engine_specs, load_engines
from usb_pasteur.hashing import hash_fd
from usb_pasteur.imageupdate import UPDATE_FOLDER as IMAGE_FOLDER
from usb_pasteur.logs import get_logger, log_event
from usb_pasteur.monitor import Action, DeviceSource
from usb_pasteur.policy import device_verdict
from usb_pasteur.quarantine import Quarantine
from usb_pasteur.report import (
    Actions,
    DeviceUsage,
    ScanReport,
    build_report,
    set_actions,
    write_report,
)
from usb_pasteur.sandbox import Sandbox
from usb_pasteur.scanner import FileResult, Scanner, ScanSummary, describe, pipeline_options
from usb_pasteur.signatures import find_stale
from usb_pasteur.sigsets import (
    UPDATE_FOLDER,
    Manifest,
    NotNewerError,
    SignatureSetError,
    current_path,
    current_set,
    install,
    installed_manifest,
    local_public_keys,
    trusted_keys,
    verify_installed,
)
from usb_pasteur.statemachine import State, StateMachine
from usb_pasteur.text import escape
from usb_pasteur.ui import Display
from usb_pasteur.ui.base import Screen
from usb_pasteur.workers import EngineInfo, WorkerPool, auto_workers

logger = get_logger("kiosk")

# Number of infected files listed on screen before asking to clean
_MAX_LISTED = 10


__all__ = [
    "Kiosk",
    "NoEngineError",
    "build_engines",
    "build_pool",
    "check_signatures",
    "stale_signatures",
]

# Absolute path: commands are never looked up in PATH
SYSTEMCTL = "/usr/bin/systemctl"
CLAMD_SERVICE = "clamav-daemon.service"


def build_engines(config: Config) -> list[Engine]:
    """Load the enabled engines in this process (configuration check).

    Raise NoEngineError or EngineError on failure.
    """
    return load_engines(engine_specs(config))


def build_pool(config: Config) -> WorkerPool:
    """The scan worker pool (not started).

    Raise NoEngineError without engine, SandboxError when the workers cannot
    be sandboxed.
    """
    specs = engine_specs(config)
    sandbox = build_sandbox(config)
    if sandbox is not None:
        sandbox.check()
    return WorkerPool(
        specs,
        pipeline_options(config),
        config.scan.workers or auto_workers(),
        config.scan.file_timeout,
        sandbox=sandbox,
    )


def signature_files(config: Config) -> list[Path]:
    """The signature files and folders read by the enabled engines.

    clamd reads its own databases (its DatabaseDirectory, in the image the
    clamav folder of the signature set).
    """
    engines = config.engines
    files: list[Path] = []
    if engines.malwarebazaar.enabled:
        files.append(engines.malwarebazaar.database)
    if engines.hashlookup.enabled:
        files.append(engines.hashlookup.bloom)
    if engines.yara.enabled:
        files += [rule_set.path for rule_set in engines.yara.rules]
    return files


def check_signatures(config: Config) -> Manifest | None:
    """Verify the installed signature set and that the engines only read it.

    Raise SignatureSetError when the set is missing, not signed by a trusted
    key, modified, or when an engine file is not part of it. Nothing is
    checked in FAKE_SCAN mode or without signatures.verify.
    """
    if config.kiosk.fake_scan or not config.signatures.verify:
        return None
    folder = config.signatures.folder
    keys = trusted_keys(config.signatures.keys) + local_public_keys(folder)
    manifest = verify_installed(folder, keys)
    current = current_path(folder)
    for path in signature_files(config):
        try:
            rel = path.relative_to(current).as_posix()
        except ValueError:
            raise SignatureSetError(f"{path} is not in the signature set ({current})") from None
        if not any(f.path == rel or f.path.startswith(rel + "/") for f in manifest.files):
            raise SignatureSetError(f"{path} is not in the signature set ({current})")
    return manifest


def restart_clamd() -> None:
    """Restart clamd, which reads its databases at start, if it runs.

    Not running, it is started with the new databases by its socket.
    """
    if not Path("/run/systemd/system").is_dir() or not Path(SYSTEMCTL).exists():
        return
    try:
        subprocess.run(  # noqa: S603  (fixed command, no shell)
            # Never an authentication prompt (polkit) for a user without the right
            [SYSTEMCTL, "--no-ask-password", "try-restart", CLAMD_SERVICE],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        log_event(logger, "clamd_restart_failed", logging.ERROR, error=str(ex))


def build_sandbox(config: Config) -> Sandbox | None:
    """The sandbox of the scan workers: the files of the enabled engines only."""
    if not config.scan.sandbox:
        return None
    engines = config.engines
    files = signature_files(config)
    writable: list[Path] = []
    if engines.clamav.enabled:
        files.append(engines.clamav.socket)
    if engines.yara.enabled and engines.yara.cache_dir is not None:
        writable.append(engines.yara.cache_dir)
    # The folder of each file: signature manifests, clamd socket re-created
    read_only = {path if path.is_dir() else path.parent for path in files}
    return Sandbox(config.scan.sandbox_user, tuple(sorted(read_only)), tuple(writable))


def make_mounter(config: Config) -> Mounter:
    device = config.device
    if device.auto_mount:
        return SystemMountWatcher(device.allowed_filesystems, device.auto_mount_wait)
    # Sandboxed workers read the files through their group (YARA-X reopens
    # the descriptor it gets)
    sandbox = build_sandbox(config)
    return Mounter(
        device.mount_point,
        device.allowed_filesystems,
        use_sudo=device.use_sudo,
        timeout=device.command_timeout,
        reader_gid=None if sandbox is None else sandbox.ids()[1],
    )


def stale_signatures(config: Config, engines: list[EngineInfo]) -> tuple[list[str], list[str]]:
    """Signature databases older than their maximum age, and those without a date.

    Return the warnings shown on the screen (a known date, too old) and the
    databases whose age is unknown (only logged: for instance a ClamAV
    database of custom signatures only, without the official CVD header).
    """
    engine_configs = {
        "malwarebazaar": config.engines.malwarebazaar.max_age_days,
        "hashlookup": config.engines.hashlookup.max_age_days,
        "clamav": config.engines.clamav.max_age_days,
        "yara": config.engines.yara.max_age_days,
    }
    warnings = []
    undated = []
    for engine in engines:
        if engine.name not in engine_configs:
            continue
        max_age = engine_configs[engine.name] or config.signatures.max_age_days
        for stale in find_stale(engine.name, engine.signatures, max_age):
            if stale.age_days == float("inf"):
                undated.append(f"{engine.name}: {stale.signature.name}")
            else:
                warnings.append(
                    f"{engine.name}: signatures {stale.signature.name} are old "
                    f"({stale.age_days:.0f} days)"
                )
    return warnings, undated


class Kiosk:
    def __init__(
        self,
        config: Config,
        display: Display,
        source: DeviceSource,
        pool: WorkerPool,
        mounter: Mounter | None = None,
        signatures_error: str | None = None,
    ) -> None:
        """pool must be started; it is stopped by its owner.

        signatures_error: why the signatures cannot be used (the pool is not
        started). The kiosk then scans nothing: it only accepts a signature
        update device.
        """
        self.config = config
        self.signatures_error = signatures_error
        # The signature set loaded by the engines: an online update installs a
        # new one, loaded when the kiosk is idle
        self.loaded_set = current_set(config.signatures.folder)
        self.loaded_manifest = installed_manifest(config.signatures.folder)
        self.display = display
        self.source = source
        self.scanner = Scanner(pool, config.limits)
        self.mounter = mounter or make_mounter(config)
        self.quarantine = (
            Quarantine(config.quarantine.folder) if config.quarantine.enabled else None
        )
        self.device: UsbDevice | None = None
        self.summary: ScanSummary | None = None
        # Files to quarantine and remove, depending on the suspicious policy
        self.to_remove: list[FileResult] = []
        self.report: ScanReport | None = None
        self.actions = Actions()
        self.machine = StateMachine(
            {
                State.START: self.on_start,
                State.WAIT: self.on_wait,
                State.INSERTED: self.on_inserted,
                State.SCAN: self.on_scan,
                State.CLEAN: self.on_clean,
                State.ERROR: self.on_error,
            }
        )

    def run(self) -> None:
        try:
            self.machine.run()
        finally:
            self._unmount()
            log_event(logger, "kiosk_stopped")

    # -- states ------------------------------------------------------------

    def on_start(self) -> State:
        installed = installed_manifest(self.config.signatures.folder)
        image_version = imageupdate.running_version()
        log_event(
            logger,
            "kiosk_started",
            version=__version__,
            image_version=image_version,
            ready=self.signatures_error is None,
            signature_set=None if installed is None else installed.serial,
            fake_scan=self.config.kiosk.fake_scan,
            engines={e.name: e.version for e in self.scanner.engines},
            signatures={
                e.name: [
                    [s.name, s.version, s.date.isoformat() if s.date else None]
                    for s in e.signatures
                ]
                for e in self.scanner.engines
            },
        )
        # The version of the image (date and time of its build); the version
        # of the Python package only outside an image (development)
        if image_version is None:
            info = [f"USB-Pasteur {__version__} (development)"]
        else:
            info = [f"USB-Pasteur {image_version}{_build_date(image_version)}"]
        if installed is None:
            info.append("Signatures: none installed")
        else:
            created = installed.created.astimezone(UTC)
            info.append(f"Signatures: set {installed.serial} of {created:%Y-%m-%d %H:%M} UTC")
        info.append(f"Engines: {describe_engines(self.scanner.engines)}")
        if self.config.kiosk.fake_scan:
            info.append("FAKE SCAN MODE - for development only, no real detection")
        if self.config.device.auto_mount:
            info.append("AUTO-MOUNT MODE - for development only, devices are not mounted read-only")
        for line in info:
            self.display.message(line)
        self.display.show_info(info)
        self._show_ready()
        return State.WAIT

    def _show_ready(self) -> None:
        self._show_waiting()
        if self.signatures_error is not None:
            log_event(logger, "signatures_unusable", logging.ERROR, error=self.signatures_error)
            self.display.message(f"NO VALID SIGNATURES: {self.signatures_error}")
            self.display.message("This kiosk cannot scan: insert a signature update device.")
            return
        warnings, undated = stale_signatures(self.config, self.scanner.engines)
        for warning in warnings:
            log_event(logger, "signatures_stale", logging.WARNING, warning=warning)
            self.display.message(f"WARNING: {warning}")
        if undated:
            log_event(logger, "signatures_undated", signatures=undated)
        self.display.message("Ready. Insert a USB device.")

    def _show_waiting(self) -> None:
        """The screen of a kiosk without device: ready, or without signatures."""
        self.display.show(Screen.READY if self.signatures_error is None else Screen.UNAVAILABLE)

    def on_wait(self) -> State:
        self._unmount()
        event = self.source.wait_event()
        if event is None:
            return State.STOP
        if event.action is Action.IDLE:
            if imageupdate.RESTART_FLAG.exists():
                return self._restart_after_update()
            self._load_new_set()
            return State.WAIT
        if event.action is Action.REMOVE:
            log_event(logger, "device_removed", node=event.device.node)
            self.device = None
            self.display.show_device(None)
            self._show_waiting()
            self.display.message("Device removed")
            return State.WAIT
        self.device = event.device
        d = event.device
        log_event(
            logger,
            "device_inserted",
            node=d.node,
            fs_type=d.fs_type,
            label=d.label,
            vendor=d.vendor,
            model=d.model,
            serial=d.serial,
        )
        self.display.show_device(d)
        self.display.show(Screen.SCANNING)
        self.display.message("Device inserted")
        return State.INSERTED

    def on_inserted(self) -> State:
        if self.device is None:
            return State.WAIT
        try:
            self.mounter.mount(self.device, read_only=True)
        except DeviceError as ex:
            log_event(logger, "mount_failed", logging.ERROR, node=self.device.node, error=str(ex))
            self.display.message(f"Cannot mount device: {ex}")
            return State.ERROR
        root = self.mounter.mount_point
        # A device may hold a maintenance request, a signature set and an
        # image update, each verified on its own: all are handled, the image
        # last as it restarts the kiosk. Such a device is never scanned.
        not_scanned = ""
        if any(
            _has_folder(root, name) and enabled
            for name, enabled in (
                (maintenance.FOLDER, True),
                (UPDATE_FOLDER, self.config.signatures.update_from_devices),
                (IMAGE_FOLDER, self.config.updates.image_from_devices),
            )
        ):
            self.display.show(Screen.SERVICE)
        if _has_folder(root, maintenance.FOLDER):
            self._export_logs(self.device)
            not_scanned = "A maintenance device is not scanned. Remove the device."
        if self.config.signatures.update_from_devices and _has_folder(root, UPDATE_FOLDER):
            self._update_signatures(root / UPDATE_FOLDER)
            not_scanned = "A signature update device is not scanned. Remove the device."
        if self.config.updates.image_from_devices and _has_folder(root, IMAGE_FOLDER):
            version = self._update_image(root / IMAGE_FOLDER)
            self.display.message("An image update device is not scanned. Remove the device.")
            self._release()
            if version is not None:
                log_event(logger, "restarting", image_version=str(version))
                imageupdate.reboot()
                return State.STOP
            return State.WAIT
        if not_scanned:
            self.display.message(not_scanned)
            self._release()
            return State.WAIT
        if self.signatures_error is not None:
            log_event(logger, "scan_refused", logging.WARNING, reason=self.signatures_error)
            self.display.message("Cannot scan: no valid signatures. Remove the device.")
            self._release()
            return State.WAIT
        return State.SCAN

    def _export_logs(self, device: UsbDevice) -> None:
        """Write the logs of the kiosk to a maintenance device (signed request)."""
        folder = self.mounter.mount_point / maintenance.FOLDER
        self.display.message("Maintenance device: verifying the request...")
        try:
            maintenance.verify(folder, self.config)
            # Read-write to write the logs: verified again, as mounted now
            self.mounter.unmount()
            self.mounter.mount(device, read_only=False)
            maintenance.verify(folder, self.config)
            self.display.message("Exporting the logs...")
            target, count = maintenance.export(folder, self.config)
        except (maintenance.MaintenanceError, DeviceError, OSError) as ex:
            log_event(logger, "maintenance_refused", logging.WARNING, reason=str(ex))
            self.display.message(f"Maintenance REFUSED: {ex}")
            return
        log_event(logger, "logs_exported", folder=target.name, files=count)
        self.display.message(f"Logs exported to {maintenance.FOLDER}/{target.name}.")

    def _update_image(self, source: Path) -> int | None:
        """Install the image update of the device; return the version to restart on."""
        running = imageupdate.running_version()
        self.display.message("Image update device: verifying the update...")
        try:
            manifest = imageupdate.stage(
                source, trusted_keys(self.config.signatures.keys), running=running
            )
        except NotNewerError as ex:
            log_event(logger, "image_not_newer", reason=str(ex))
            self.display.message(f"System already up to date: {ex}")
            return None
        except (SignatureSetError, OSError) as ex:
            log_event(logger, "image_update_refused", logging.WARNING, reason=str(ex))
            self.display.message(f"Image update REFUSED: {ex}")
            return None
        self.display.message(f"Installing the system version {manifest.serial}...")
        try:
            imageupdate.apply()
        except imageupdate.ImageUpdateError as ex:
            log_event(logger, "image_update_failed", logging.ERROR, error=str(ex))
            self.display.message(f"Image update FAILED: {ex}")
            return None
        log_event(logger, "image_update_installed", version=manifest.serial, previous=running)
        self.display.message(f"System version {manifest.serial} installed: the kiosk restarts.")
        return manifest.serial

    def _update_signatures(self, source: Path) -> None:
        """Install the signature set of the device, then reload the engines."""
        folder = self.config.signatures.folder
        before = installed_manifest(folder)
        self.display.message("Signature update device: verifying the signature set...")
        try:
            manifest = install(source, folder, trusted_keys(self.config.signatures.keys))
        except NotNewerError as ex:
            log_event(logger, "signatures_not_newer", reason=str(ex))
            self.display.message(f"Signatures already up to date: {ex}")
            return
        except (SignatureSetError, OSError) as ex:
            log_event(logger, "signatures_refused", logging.WARNING, reason=str(ex))
            self.display.message(f"Signature update REFUSED: {ex}")
            return
        log_event(
            logger,
            "signatures_installed",
            serial=manifest.serial,
            created=manifest.created.isoformat(),
            files=len(manifest.files),
            previous=None if before is None else before.serial,
        )
        self.display.message(f"Signatures updated: set {manifest.serial}")
        self._reload_engines(before, manifest)

    def _restart_after_update(self) -> State:
        """An image update was installed online: restart, the kiosk being idle."""
        try:
            version = imageupdate.RESTART_FLAG.read_text().strip()
        except OSError:
            version = "?"
        log_event(logger, "restarting", image_version=version)
        self.display.show(Screen.SERVICE)
        self.display.message(f"System version {version} installed: the kiosk restarts.")
        imageupdate.reboot()
        return State.STOP

    def _load_new_set(self) -> None:
        """Load a signature set installed meanwhile (online update)."""
        folder = self.config.signatures.folder
        current = current_set(folder)
        after = installed_manifest(folder)
        if current == self.loaded_set or after is None:
            return
        log_event(
            logger,
            "signatures_changed",
            serial=after.serial,
            previous=None if self.loaded_manifest is None else self.loaded_manifest.serial,
        )
        self.display.message(f"New signatures installed: set {after.serial}")
        self._reload_engines(self.loaded_manifest, after)

    def _reload_engines(self, before: Manifest | None, after: Manifest) -> None:
        self.loaded_set = current_set(self.config.signatures.folder)
        self.loaded_manifest = after
        # In FAKE_SCAN mode, clamd is not used
        clamav = self.config.engines.clamav.enabled and not self.config.kiosk.fake_scan
        if clamav and _changed(before, after, "clamav/"):
            restart_clamd()
        pool = self.scanner.pool
        pool.stop()
        try:
            check_signatures(self.config)
            pool.start()
        except (SignatureSetError, EngineError) as ex:
            pool.stop()
            self.signatures_error = str(ex)
        else:
            self.signatures_error = None
            log_event(logger, "engines_reloaded", engines={e.name: e.version for e in pool.engines})
        self._show_ready()

    def on_scan(self) -> State:
        root = self.mounter.mount_point
        try:
            st = os.statvfs(root)
        except OSError as ex:
            log_event(logger, "statvfs_failed", logging.ERROR, error=str(ex))
            self.display.message(f"Cannot read device: {ex}")
            return State.ERROR
        size = st.f_frsize * st.f_blocks
        used = max(1, st.f_frsize * (st.f_blocks - st.f_bfree))
        started = datetime.now(UTC)
        # Signature versions as they are at the start of the scan
        engines = self.scanner.pool.engine_info()
        self.display.show_usage(size, used)
        log_event(logger, "scan_started", size=size, used=used)
        self.display.message("Scanning...")
        self.display.progress(0)

        def on_progress(result: FileResult, done: int, total: int) -> None:
            self.display.message(f"[{done}/{total}] {describe(result)}")
            self.display.file_scanned(result, done, total)
            self.display.progress(min(99, done * 100 // max(1, total)))

        self.summary = self.scanner.scan_tree(root, on_progress)
        self.display.progress(100)
        s = self.summary
        self.display.message(
            f"Scan done in {s.duration:.1f}s, {len(s.files)} files scanned, "
            f"{len(s.infected)} files infected, {len(s.suspicious)} files suspicious, "
            f"{len(s.unscanned)} files not fully scanned"
        )
        log_event(logger, "device_verdict", verdict=device_verdict(s).value, complete=s.complete)
        self.to_remove = s.infected
        if self.config.scan.suspicious == "block":
            self.to_remove = s.infected + s.suspicious
        self.report = build_report(
            self.config,
            self.device,
            DeviceUsage(size, used),
            engines,
            s,
            started,
            datetime.now(UTC),
            self.scanner.pool.size,
        )
        self.actions = Actions()
        self._save_report()
        if self.quarantine is not None:
            try:
                self.actions.quarantine_folder = self.quarantine.store(
                    self.to_remove,
                    root,
                    self.report.report_id,
                    self.report.path,
                )
                if self.actions.quarantine_folder is not None:
                    self.actions.quarantined = list(self.to_remove)
            except OSError as ex:
                log_event(logger, "quarantine_failed", logging.ERROR, error=str(ex))
            self._save_report()
        return State.CLEAN

    def on_clean(self) -> State:
        if self.summary is None:
            return State.WAIT
        infected = self.to_remove
        if self.config.scan.suspicious == "warn" and self.summary.suspicious:
            suspicious = self.summary.suspicious
            log_event(logger, "suspicious_files", logging.WARNING, count=len(suspicious))
            self._list(
                f"WARNING: {len(suspicious)} suspicious files, use with caution:", suspicious
            )
        not_verified = self._check_complete(self.summary)
        if not infected:
            self._release()
            if not_verified:
                self.display.show(Screen.NOT_VERIFIED, self.summary.unscanned)
                self.display.message("DEVICE NOT VERIFIED: do not use it. Remove the device.")
            else:
                self.display.show(*self._clean_screen())
                self.display.message("No infected file found. You can remove the device.")
            return State.WAIT

        log_event(logger, "infected_files", count=len(infected))
        self.display.show(Screen.THREATS, infected)
        self._list(f"{len(infected)} infected files detected:", infected)
        # The device is not mounted while the user decides: it can be removed
        try:
            self.mounter.unmount()
        except DeviceError as ex:
            log_event(logger, "unmount_failed", logging.ERROR, error=str(ex))
            self.display.message(f"Cannot unmount device: {ex}")
            return State.ERROR
        self.display.confirm("PRESS A KEY OR TOUCH THE SCREEN TO CLEAN")

        device = self.device
        if device is None or not self.mounter.device_present(device):
            log_event(logger, "device_removed_before_clean", logging.WARNING)
            self.display.show(Screen.NOT_CLEANED, infected)
            self.display.message("Device removed before cleaning: NOT CLEANED")
            self._release()
            return State.WAIT
        try:
            # Read-write only to remove the infected files
            self.mounter.mount(device, read_only=False)
        except DeviceError as ex:
            log_event(logger, "mount_rw_failed", logging.ERROR, error=str(ex))
            self.display.message(f"Cannot clean device: {ex}")
            return State.ERROR
        log_event(logger, "device_mounted_rw", node=device.node)
        self.display.show(Screen.CLEANING)

        for result in infected:
            if self._remove(result):
                self.actions.removed.append(result)
            else:
                self.actions.remove_failed.append(result)
        removed = len(self.actions.removed)
        self.actions.cleaned = removed == len(infected)
        self._save_report()
        self._release()
        log_event(logger, "device_cleaned", removed=removed, infected=len(infected))
        if removed == len(infected) and not_verified:
            self.display.show(Screen.NOT_VERIFIED, self.summary.unscanned)
            self.display.message(
                "Device cleaned, but NOT VERIFIED: do not use it. Remove the device."
            )
        elif removed == len(infected):
            screen, kept = self._clean_screen()
            if screen is Screen.WARNING:
                self.display.show(Screen.WARNING, kept)
            else:
                self.display.show(Screen.CLEANED, self.actions.removed)
            self.display.message("Device cleaned! You can remove the device.")
        else:
            self.display.show(Screen.NOT_CLEANED, self.actions.remove_failed)
            self.display.message(f"Device NOT cleaned: {len(infected) - removed} files remain")
        return State.WAIT

    def on_error(self) -> State:
        self._release()
        self.display.show(Screen.ERROR)
        self.display.message("Error: please remove the device.")
        return State.WAIT

    # -- helpers -----------------------------------------------------------

    def _clean_screen(self) -> tuple[Screen, list[FileResult]]:
        """No infected file is left: a warning for the files kept (warn policies)."""
        files = [] if self.summary is None else self.summary.files
        kept = [
            f
            for f in files
            if f not in self.to_remove and (f.verdict is Verdict.SUSPICIOUS or f.unscanned)
        ]
        return (Screen.WARNING, kept) if kept else (Screen.CLEAN, [])

    def _save_report(self) -> None:
        """Write the scan report; a failure is logged and shown, not fatal."""
        if self.report is None:
            return
        set_actions(self.report, self.actions)
        try:
            path = write_report(self.report, self.config.report.folder)
        except OSError as ex:
            log_event(logger, "report_failed", logging.ERROR, error=str(ex))
            self.display.message(f"WARNING: cannot write the scan report: {ex.strerror}")
            return
        log_event(logger, "report_written", report_id=self.report.report_id, path=str(path))

    def _check_complete(self, summary: ScanSummary) -> bool:
        """Report files that were not fully scanned.

        Return True when the device must be rejected (scan.on_error = "block").
        Unscanned files are never removed: they are not known to be malicious.
        """
        if summary.complete:
            return False
        unscanned = summary.unscanned
        block = self.config.scan.on_error == "block"
        log_event(
            logger,
            "device_not_verified" if block else "device_incomplete",
            logging.WARNING,
            files=len(unscanned),
            reasons=summary.incomplete_reasons,
        )
        if block:
            title = f"{len(unscanned)} files could not be fully scanned:"
        else:
            title = f"WARNING: {len(unscanned)} files could not be fully scanned, use with caution:"
        self._list(title, unscanned)
        for reason in summary.incomplete_reasons:
            self.display.message(f"Not scanned: {reason}")
        return block

    def _list(self, title: str, results: list[FileResult]) -> None:
        self.display.message(title)
        for result in results[:_MAX_LISTED]:
            self.display.message(result.rel_path)
        if len(results) > _MAX_LISTED:
            self.display.message("...")

    def _remove(self, result: FileResult) -> bool:
        """Remove an infected file, if it is still the file that was scanned.

        The device was unmounted while waiting for the user, who may have
        inserted another device: the content must have the scanned SHA-256.
        """
        root = self.mounter.mount_point
        path = root / result.rel_path
        name = escape(result.rel_path)
        # Never follow a link out of the device
        if not Path(os.path.realpath(path)).is_relative_to(os.path.realpath(root)):
            log_event(logger, "remove_refused", logging.ERROR, path=name, reason="outside")
            return False
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                sha256 = hash_fd(fd).sha256
            finally:
                os.close(fd)
            if result.info is None or sha256 != result.info.sha256:
                log_event(
                    logger, "remove_refused", logging.ERROR, path=name, reason="content changed"
                )
                self.display.message(f"Not removed, changed since the scan: {result.rel_path}")
                return False
            path.unlink()
        except OSError as ex:
            log_event(logger, "remove_failed", logging.ERROR, path=name, error=ex.strerror)
            self.display.message(f"Could not remove {result.rel_path}: {ex.strerror}")
            return False
        log_event(logger, "file_removed", path=name)
        self.display.message(f"{result.rel_path} removed")
        return True

    def _release(self) -> None:
        """Unmount the device, then eject it (device.eject)."""
        self._unmount()
        if self.device is None or not self.config.device.eject:
            return
        try:
            self.mounter.eject(self.device)
        except DeviceError as ex:
            # Unmounted and synced: the device can still be removed safely
            log_event(logger, "eject_failed", logging.WARNING, error=str(ex))

    def _unmount(self) -> None:
        try:
            self.mounter.unmount()
        except DeviceError as ex:
            log_event(logger, "unmount_failed", logging.ERROR, error=str(ex))


def describe_engines(engines: list[EngineInfo]) -> str:
    """The enabled engines, with the version of the scanning programs (ClamAV, YARA-X)."""
    if not engines:
        return "none loaded (no valid signatures)"
    names = []
    for engine in engines:
        # The version of a hash engine is the format of its database
        version = engine.version if engine.kind is EngineKind.CONTENT else ""
        names.append(
            f"{engine.name} ({version})" if version and not version.isdigit() else engine.name
        )
    return ", ".join(names)


def _build_date(version: int) -> str:
    """The date of a version made of the time of its build: " of 2026-10-05 14:13 UTC"."""
    try:
        built = datetime.strptime(str(version), "%Y%m%d%H%M%S")
    except ValueError:
        return ""
    return f" of {built:%Y-%m-%d %H:%M} UTC"


def _has_folder(root: Path, name: str) -> bool:
    """The device holds that folder at its root (a folder, not a link)."""
    try:
        return stat.S_ISDIR(os.lstat(root / name).st_mode)
    except OSError:
        return False


def _changed(before: Manifest | None, after: Manifest, prefix: str) -> bool:
    def files(manifest: Manifest | None) -> set[tuple[str, str]]:
        if manifest is None:
            return set()
        return {(f.path, f.sha256) for f in manifest.files if f.path.startswith(prefix)}

    return files(before) != files(after)
