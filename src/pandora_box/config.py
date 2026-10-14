"""TOML configuration, loaded and validated at startup."""

from __future__ import annotations

import re
import socket
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar

DEFAULT_CONFIG_PATH = Path("/etc/pandora-box/pandora-box.toml")

INTERFACES = ("framebuffer", "curses", "console")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
SUSPICIOUS_POLICIES = ("block", "warn")
ERROR_POLICIES = ("block", "warn")
SUPPORTED_FILESYSTEMS = ("vfat", "exfat", "ntfs", "ext4")
CLAMD_MODES = ("auto", "fildes", "instream")
COMPILE_ERROR_POLICIES = ("fail", "skip_rule")

# Signed signature sets installed on the kiosk (pandora_box.sigsets): the
# engines read the files of the current one
SIGNATURES_ROOT = Path("/var/lib/pandora-box-signatures")
SIGNATURES_DIR = SIGNATURES_ROOT / "current"
# Public keys trusted to sign signature sets (in the image, under dm-verity)
SIGNATURE_KEYS = Path("/usr/share/pandora-box/keys")

_RULE_SET_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

T = TypeVar("T")


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


@dataclass(frozen=True)
class KioskConfig:
    name: str = field(default_factory=socket.gethostname)
    fake_scan: bool = False
    interface: str = "framebuffer"


@dataclass(frozen=True)
class DeviceConfig:
    mount_point: Path = Path("/media/pandora-box")
    use_sudo: bool = False
    allowed_filesystems: tuple[str, ...] = SUPPORTED_FILESYSTEMS
    command_timeout: float = 60.0
    # Eject the device when the scan (and the cleaning) is over
    eject: bool = True
    # Development only: use the mount made by the system (desktop automount)
    # instead of mounting the device read-only; mount_point is then ignored
    auto_mount: bool = False
    auto_mount_wait: float = 15.0


@dataclass(frozen=True)
class ScanConfig:
    # Files scanned at the same time; 0: automatic (workers.auto_workers: one
    # per CPU, as many as the memory allows)
    workers: int = 0
    # Maximum time to scan one file with all engines, in seconds
    file_timeout: float = 300.0
    # "block": suspicious files are quarantined and removed like malicious ones
    # "warn": they are only reported to the user
    suspicious: str = "block"
    # Files that could not be fully scanned (engine error, timeout, limit):
    # "block": the device is reported as NOT VERIFIED and must not be used
    # "warn": the user is warned and the files are listed
    on_error: str = "block"
    fake_delay: float = 0.0
    # Run the scan workers in a bubblewrap sandbox, as sandbox_user, without
    # network and with a system call filter (needs root, bwrap and setpriv)
    sandbox: bool = True
    sandbox_user: str = "pandora-box-scan"


@dataclass(frozen=True)
class LimitsConfig:
    max_file_size: int = 1024**3
    max_files: int = 100_000
    max_depth: int = 64


@dataclass(frozen=True)
class PolicyConfig:
    # Number of engines that must report a file as malicious; with fewer
    # positive engines, the file is only suspicious
    min_malicious_engines: int = 1


@dataclass(frozen=True)
class ReportConfig:
    folder: Path = Path("/var/lib/pandora-box/reports")


@dataclass(frozen=True)
class SignaturesConfig:
    # Warn at startup when a signature database is older than this
    max_age_days: float = 7.0
    # Installed signature sets, and the folder of the trusted public keys
    folder: Path = SIGNATURES_ROOT
    keys: Path = SIGNATURE_KEYS
    # Verify the installed set at startup (signature and every file), and
    # refuse engine files that are not part of it
    verify: bool = True
    # Install the signed set found on a device (pandora-box-signatures/)
    update_from_devices: bool = True


@dataclass(frozen=True)
class MalwareBazaarConfig:
    enabled: bool = True
    database: Path = SIGNATURES_DIR / "malwarebazaar" / "malwarebazaar.sha256.bin"
    max_age_days: float | None = None


@dataclass(frozen=True)
class HashlookupConfig:
    enabled: bool = True
    bloom: Path = SIGNATURES_DIR / "hashlookup" / "hashlookup-full.bloom"
    # Do not run the content engines (ClamAV, YARA) on known files. Off by
    # default: known is not benign (the EICAR test file, tools, malware are
    # in the CIRCL filter), it only saves time on devices full of software
    skip_content_engines: bool = False
    # The CIRCL Bloom filter is updated monthly
    max_age_days: float | None = 45.0


@dataclass(frozen=True)
class ClamavConfig:
    enabled: bool = True
    socket: Path = Path("/run/clamav/clamd.ctl")
    mode: str = "auto"
    timeout: float = 120.0
    # Must not exceed MaxFileSize, MaxScanSize and StreamMaxLength of clamd.conf
    max_file_size: int = 100 * 1024**2
    suspicious_names: tuple[str, ...] = ("PUA.*", "Heuristics.*")
    # Not fully scanned: beyond the limits of clamd, encrypted archive or
    # document (clamd.conf AlertEncrypted)
    error_names: tuple[str, ...] = ("Heuristics.Limits.Exceeded.*", "Heuristics.Encrypted.*")
    max_age_days: float | None = None


@dataclass(frozen=True)
class YaraRuleSet:
    name: str
    path: Path


@dataclass(frozen=True)
class YaraConfig:
    enabled: bool = True
    rules: tuple[YaraRuleSet, ...] = (
        YaraRuleSet("yara-forge", SIGNATURES_DIR / "yara" / "yara-forge" / "yara-rules-core.yar"),
    )
    cache_dir: Path | None = Path("/var/cache/pandora-box/yara")
    timeout: float = 60.0
    on_compile_error: str = "fail"
    exclude: tuple[str, ...] = ()
    malicious_score: int = 75
    suspicious_score: int = 40
    default_score: int = 60
    max_age_days: float | None = None


@dataclass(frozen=True)
class HeuristicsConfig:
    # Structure of the files: PDF with JavaScript, disguised executables
    enabled: bool = True


@dataclass(frozen=True)
class EnginesConfig:
    malwarebazaar: MalwareBazaarConfig = field(default_factory=MalwareBazaarConfig)
    hashlookup: HashlookupConfig = field(default_factory=HashlookupConfig)
    clamav: ClamavConfig = field(default_factory=ClamavConfig)
    yara: YaraConfig = field(default_factory=YaraConfig)
    heuristics: HeuristicsConfig = field(default_factory=HeuristicsConfig)


@dataclass(frozen=True)
class QuarantineConfig:
    enabled: bool = True
    folder: Path = Path("/var/lib/pandora-box/quarantine")


@dataclass(frozen=True)
class LoggingConfig:
    file: Path | None = Path("/var/log/pandora-box/pandora-box.log")
    level: str = "INFO"


# Sources of the signatures a kiosk can download itself (pandora_box.publish)
SIGNATURE_SOURCES = ("clamav", "yara-forge", "signature-base", "malwarebazaar", "hashlookup")


@dataclass(frozen=True)
class UpdatesConfig:
    # Online signature updates (pandora-box-update.service, image profile
    # "online"): off by default, a kiosk works offline
    enabled: bool = False
    # Either the kiosk downloads the signatures from their sources (sources,
    # docs/signatures.md) and signs the set with its own key, or it
    # downloads a signed set published by pandora-box-signatures publish
    # (url: its folder, HTTPS recommended, HTTP accepted as the set is signed)
    sources: tuple[str, ...] = ()
    url: str = ""
    # URLs in place of the default ones of the sources, e.g. a company mirror
    # ("clamav": a freshclam private mirror)
    mirrors: dict[str, str] = field(default_factory=dict)
    # Folder of a published image update (pandora_box.imageupdate): "" for no
    # online image updates
    image_url: str = ""
    # HTTP(S) proxy, e.g. http://proxy.example.org:3128 ("": direct)
    proxy: str = ""
    # Timeout of each request, in seconds
    timeout: float = 300.0
    # Install the signed image update of a device (pandora-box-image/), then
    # restart (A/B updates, pandora_box.imageupdate)
    image_from_devices: bool = True


_LANGUAGE = re.compile(r"^[a-z]{2}$")


@dataclass(frozen=True)
class DisplayConfig:
    # Theme folder (pandora_box.ui.theme): logo, colors, texts and images in
    # place of the built-in ones; None for the built-in theme
    theme: Path | None = None
    # Language of the texts of the theme
    language: str = "en"
    # Framebuffer device of the graphical interface
    framebuffer: Path = Path("/dev/fb0")
    # A result stays on the screen until its device is removed, then this
    # long (seconds) before the waiting screen
    return_seconds: float = 5.0


@dataclass(frozen=True)
class Config:
    kiosk: KioskConfig = field(default_factory=KioskConfig)
    device: DeviceConfig = field(default_factory=DeviceConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    engines: EnginesConfig = field(default_factory=EnginesConfig)
    signatures: SignaturesConfig = field(default_factory=SignaturesConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    quarantine: QuarantineConfig = field(default_factory=QuarantineConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    updates: UpdatesConfig = field(default_factory=UpdatesConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)


def load_config(path: Path) -> Config:
    """Read and validate the configuration file."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError as ex:
        raise ConfigError(f"configuration file not found: {path}") from ex
    except tomllib.TOMLDecodeError as ex:
        raise ConfigError(f"{path}: invalid TOML: {ex}") from ex
    except OSError as ex:
        raise ConfigError(f"{path}: {ex.strerror}") from ex
    return parse_config(data)


def parse_config(data: dict[str, Any]) -> Config:
    """Build a validated Config from a parsed TOML document."""
    _reject_unknown(data, {f.name for f in fields(Config)}, "")
    for name, value in data.items():
        if not isinstance(value, dict):
            raise ConfigError(f"[{name}] must be a table")

    kiosk = data.get("kiosk", {})
    _reject_unknown(kiosk, {"name", "fake_scan", "interface"}, "kiosk")
    kiosk_cfg = KioskConfig(
        name=_get(kiosk, "kiosk", "name", str, KioskConfig().name),
        fake_scan=_get(kiosk, "kiosk", "fake_scan", bool, False),
        interface=_choice(kiosk, "kiosk", "interface", INTERFACES, KioskConfig.interface),
    )
    if not kiosk_cfg.name.strip():
        raise ConfigError("kiosk.name must not be empty")

    device = data.get("device", {})
    _reject_unknown(
        device,
        {
            "mount_point",
            "use_sudo",
            "allowed_filesystems",
            "command_timeout",
            "eject",
            "auto_mount",
            "auto_mount_wait",
        },
        "device",
    )
    filesystems = _get(device, "device", "allowed_filesystems", list, list(SUPPORTED_FILESYSTEMS))
    for fs in filesystems:
        if fs not in SUPPORTED_FILESYSTEMS:
            raise ConfigError(
                f"device.allowed_filesystems: unsupported filesystem {fs!r} "
                f"(supported: {', '.join(SUPPORTED_FILESYSTEMS)})"
            )
    device_cfg = DeviceConfig(
        mount_point=_path(device, "device", "mount_point", DeviceConfig.mount_point),
        use_sudo=_get(device, "device", "use_sudo", bool, False),
        allowed_filesystems=tuple(filesystems),
        command_timeout=_positive(device, "device", "command_timeout", 60.0),
        eject=_get(device, "device", "eject", bool, True),
        auto_mount=_get(device, "device", "auto_mount", bool, False),
        auto_mount_wait=_positive(device, "device", "auto_mount_wait", 15.0),
    )

    scan = data.get("scan", {})
    if "max_file_size" in scan:
        raise ConfigError("scan.max_file_size has moved to limits.max_file_size")
    _reject_unknown(
        scan,
        {
            "workers",
            "file_timeout",
            "suspicious",
            "on_error",
            "fake_delay",
            "sandbox",
            "sandbox_user",
        },
        "scan",
    )
    value = scan.get("workers", "auto")
    if value == "auto":
        workers = 0
    elif isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 64:
        workers = value
    else:
        raise ConfigError('scan.workers must be "auto" or between 1 and 64')
    fake_delay = _number(scan, "scan", "fake_delay", 0.0)
    if fake_delay < 0:
        raise ConfigError("scan.fake_delay must not be negative")
    scan_cfg = ScanConfig(
        workers=workers,
        file_timeout=_positive(scan, "scan", "file_timeout", ScanConfig.file_timeout),
        suspicious=_choice(scan, "scan", "suspicious", SUSPICIOUS_POLICIES, "block"),
        on_error=_choice(scan, "scan", "on_error", ERROR_POLICIES, "block"),
        fake_delay=fake_delay,
        sandbox=_get(scan, "scan", "sandbox", bool, True),
        sandbox_user=_get(scan, "scan", "sandbox_user", str, ScanConfig.sandbox_user),
    )
    if not scan_cfg.sandbox_user.strip():
        raise ConfigError("scan.sandbox_user must not be empty")

    limits = data.get("limits", {})
    _reject_unknown(limits, {"max_file_size", "max_files", "max_depth"}, "limits")
    limits_cfg = LimitsConfig(
        max_file_size=_positive_int(limits, "limits", "max_file_size", LimitsConfig.max_file_size),
        max_files=_positive_int(limits, "limits", "max_files", LimitsConfig.max_files),
        max_depth=_positive_int(limits, "limits", "max_depth", LimitsConfig.max_depth),
    )

    policy = data.get("policy", {})
    _reject_unknown(policy, {"min_malicious_engines"}, "policy")
    policy_cfg = PolicyConfig(
        min_malicious_engines=_positive_int(policy, "policy", "min_malicious_engines", 1),
    )

    signatures = data.get("signatures", {})
    _reject_unknown(
        signatures,
        {"max_age_days", "folder", "keys", "verify", "update_from_devices"},
        "signatures",
    )
    signatures_cfg = SignaturesConfig(
        max_age_days=_positive(signatures, "signatures", "max_age_days", 7.0),
        folder=_path(signatures, "signatures", "folder", SIGNATURES_ROOT),
        keys=_path(signatures, "signatures", "keys", SIGNATURE_KEYS),
        verify=_get(signatures, "signatures", "verify", bool, True),
        update_from_devices=_get(signatures, "signatures", "update_from_devices", bool, True),
    )

    report = data.get("report", {})
    _reject_unknown(report, {"folder"}, "report")
    report_cfg = ReportConfig(folder=_path(report, "report", "folder", ReportConfig.folder))

    quarantine = data.get("quarantine", {})
    _reject_unknown(quarantine, {"enabled", "folder"}, "quarantine")
    quarantine_cfg = QuarantineConfig(
        enabled=_get(quarantine, "quarantine", "enabled", bool, True),
        folder=_path(quarantine, "quarantine", "folder", QuarantineConfig.folder),
    )

    log = data.get("logging", {})
    _reject_unknown(log, {"file", "level"}, "logging")
    log_file: Path | None = _path(log, "logging", "file", LoggingConfig.file or Path())
    if "file" in log and log["file"] == "":
        log_file = None
    logging_cfg = LoggingConfig(
        file=log_file,
        level=_choice(log, "logging", "level", LOG_LEVELS, "INFO"),
    )

    return Config(
        kiosk=kiosk_cfg,
        device=device_cfg,
        scan=scan_cfg,
        limits=limits_cfg,
        policy=policy_cfg,
        engines=_parse_engines(data.get("engines", {})),
        signatures=signatures_cfg,
        report=report_cfg,
        quarantine=quarantine_cfg,
        logging=logging_cfg,
        updates=_parse_updates(data.get("updates", {})),
        display=_parse_display(data.get("display", {})),
    )


def _parse_display(display: dict[str, Any]) -> DisplayConfig:
    _reject_unknown(display, {"theme", "language", "framebuffer", "return_seconds"}, "display")
    theme = None
    if display.get("theme", "") != "":
        theme = _path(display, "display", "theme", Path())
    language = _get(display, "display", "language", str, DisplayConfig.language)
    if not _LANGUAGE.match(language):
        raise ConfigError('display.language must be a language code, e.g. "en" or "fr"')
    return DisplayConfig(
        theme=theme,
        language=language,
        framebuffer=_path(display, "display", "framebuffer", DisplayConfig.framebuffer),
        return_seconds=_positive(
            display, "display", "return_seconds", DisplayConfig.return_seconds
        ),
    )


def _parse_updates(updates: dict[str, Any]) -> UpdatesConfig:
    _reject_unknown(
        updates,
        {"enabled", "sources", "url", "mirrors", "image_url", "proxy", "timeout",
         "image_from_devices"},
        "updates",
    )  # fmt: skip
    sources = _get(updates, "updates", "sources", list, [])
    for source in sources:
        if source not in SIGNATURE_SOURCES:
            raise ConfigError(
                f"updates.sources: unknown source {source!r} "
                f"(known: {', '.join(SIGNATURE_SOURCES)})"
            )
    mirrors = _get(updates, "updates", "mirrors", dict, {})
    for source, url in mirrors.items():
        if source not in SIGNATURE_SOURCES:
            raise ConfigError(f"updates.mirrors: unknown source {source!r}")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ConfigError(f"updates.mirrors.{source} must be an http:// or https:// URL")
    config = UpdatesConfig(
        enabled=_get(updates, "updates", "enabled", bool, False),
        sources=tuple(dict.fromkeys(sources)),
        url=_get(updates, "updates", "url", str, ""),
        mirrors=dict(mirrors),
        image_url=_get(updates, "updates", "image_url", str, ""),
        proxy=_get(updates, "updates", "proxy", str, ""),
        timeout=_positive(updates, "updates", "timeout", UpdatesConfig.timeout),
        image_from_devices=_get(updates, "updates", "image_from_devices", bool, True),
    )
    for key, value in (
        ("url", config.url),
        ("image_url", config.image_url),
        ("proxy", config.proxy),
    ):
        if value and not value.startswith(("https://", "http://")):
            raise ConfigError(f"updates.{key} must be an http:// or https:// URL")
    if config.sources and config.url:
        raise ConfigError("updates: sources (download from the sources) or url, not both")
    if config.enabled and not (config.sources or config.url):
        raise ConfigError("updates.sources or updates.url is required when updates are enabled")
    return config


def _parse_engines(engines: dict[str, Any]) -> EnginesConfig:
    _reject_unknown(engines, {f.name for f in fields(EnginesConfig)}, "engines")
    for name, value in engines.items():
        if not isinstance(value, dict):
            raise ConfigError(f"[engines.{name}] must be a table")

    section = "engines.malwarebazaar"
    mb = engines.get("malwarebazaar", {})
    _reject_unknown(mb, {"enabled", "database", "max_age_days"}, section)
    mb_cfg = MalwareBazaarConfig(
        enabled=_get(mb, section, "enabled", bool, True),
        database=_path(mb, section, "database", MalwareBazaarConfig.database),
        max_age_days=_optional_positive(mb, section, "max_age_days", None),
    )

    section = "engines.hashlookup"
    hl = engines.get("hashlookup", {})
    _reject_unknown(hl, {"enabled", "bloom", "skip_content_engines", "max_age_days"}, section)
    hl_cfg = HashlookupConfig(
        enabled=_get(hl, section, "enabled", bool, True),
        bloom=_path(hl, section, "bloom", HashlookupConfig.bloom),
        skip_content_engines=_get(
            hl, section, "skip_content_engines", bool, HashlookupConfig.skip_content_engines
        ),
        max_age_days=_optional_positive(hl, section, "max_age_days", HashlookupConfig.max_age_days),
    )

    section = "engines.clamav"
    av = engines.get("clamav", {})
    _reject_unknown(
        av,
        {
            "enabled",
            "socket",
            "mode",
            "timeout",
            "max_file_size",
            "suspicious_names",
            "error_names",
            "max_age_days",
        },
        section,
    )
    av_cfg = ClamavConfig(
        enabled=_get(av, section, "enabled", bool, True),
        socket=_path(av, section, "socket", ClamavConfig.socket),
        mode=_choice(av, section, "mode", CLAMD_MODES, "auto"),
        timeout=_positive(av, section, "timeout", ClamavConfig.timeout),
        max_file_size=_positive_int(av, section, "max_file_size", ClamavConfig.max_file_size),
        suspicious_names=tuple(
            _get(av, section, "suspicious_names", list, list(ClamavConfig.suspicious_names))
        ),
        error_names=tuple(_get(av, section, "error_names", list, list(ClamavConfig.error_names))),
        max_age_days=_optional_positive(av, section, "max_age_days", None),
    )

    section = "engines.yara"
    yr = engines.get("yara", {})
    _reject_unknown(
        yr,
        {
            "enabled",
            "rules",
            "cache_dir",
            "timeout",
            "on_compile_error",
            "exclude",
            "malicious_score",
            "suspicious_score",
            "default_score",
            "max_age_days",
        },
        section,
    )
    cache_dir: Path | None = _path(yr, section, "cache_dir", YaraConfig.cache_dir or Path())
    if yr.get("cache_dir") == "":
        cache_dir = None
    yr_cfg = YaraConfig(
        enabled=_get(yr, section, "enabled", bool, True),
        rules=_rule_sets(yr, section),
        cache_dir=cache_dir,
        timeout=_positive(yr, section, "timeout", YaraConfig.timeout),
        on_compile_error=_choice(yr, section, "on_compile_error", COMPILE_ERROR_POLICIES, "fail"),
        exclude=tuple(_get(yr, section, "exclude", list, [])),
        malicious_score=_score(yr, section, "malicious_score", YaraConfig.malicious_score),
        suspicious_score=_score(yr, section, "suspicious_score", YaraConfig.suspicious_score),
        default_score=_score(yr, section, "default_score", YaraConfig.default_score),
        max_age_days=_optional_positive(yr, section, "max_age_days", None),
    )
    if yr_cfg.suspicious_score > yr_cfg.malicious_score:
        raise ConfigError(f"{section}.suspicious_score must not exceed malicious_score")

    section = "engines.heuristics"
    hr = engines.get("heuristics", {})
    _reject_unknown(hr, {"enabled"}, section)
    hr_cfg = HeuristicsConfig(enabled=_get(hr, section, "enabled", bool, True))
    return EnginesConfig(
        malwarebazaar=mb_cfg, hashlookup=hl_cfg, clamav=av_cfg, yara=yr_cfg, heuristics=hr_cfg
    )


def _rule_sets(table: dict[str, Any], section: str) -> tuple[YaraRuleSet, ...]:
    if "rules" not in table:
        return YaraConfig.rules
    value = table["rules"]
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{section}.rules must be a non-empty list of tables")
    rule_sets: list[YaraRuleSet] = []
    for index, item in enumerate(value):
        where = f"{section}.rules[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{where} must be a table with name and path")
        _reject_unknown(item, {"name", "path"}, where)
        if "name" not in item or "path" not in item:
            raise ConfigError(f"{where} needs a name and a path")
        name = _get(item, where, "name", str, "")
        if not _RULE_SET_NAME.match(name):
            raise ConfigError(f"{where}.name must only contain letters, digits, '-' and '_'")
        if name in {r.name for r in rule_sets}:
            raise ConfigError(f"{section}.rules: duplicate name {name!r}")
        path = _path(item, where, "path", Path())
        if not item["path"]:
            raise ConfigError(f"{where}.path must not be empty")
        rule_sets.append(YaraRuleSet(name, path))
    return tuple(rule_sets)


def _reject_unknown(table: dict[str, Any], allowed: Any, section: str) -> None:
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        where = f"[{section}]" if section else "top level"
        raise ConfigError(f"unknown key(s) in {where}: {', '.join(unknown)}")


def _get(table: dict[str, Any], section: str, key: str, kind: type[T], default: T) -> T:
    if key not in table:
        return default
    value = table[key]
    # bool is a subclass of int: reject it explicitly for integer settings
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ConfigError(f"{section}.{key} must be of type {kind.__name__}")
    if kind is list and not all(isinstance(v, str) for v in value):  # type: ignore[attr-defined]
        raise ConfigError(f"{section}.{key} must be a list of strings")
    return value


def _number(table: dict[str, Any], section: str, key: str, default: float) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{section}.{key} must be a number")
    return float(value)


def _positive(table: dict[str, Any], section: str, key: str, default: float) -> float:
    value = _number(table, section, key, default)
    if value <= 0:
        raise ConfigError(f"{section}.{key} must be positive")
    return value


def _optional_positive(
    table: dict[str, Any], section: str, key: str, default: float | None
) -> float | None:
    if key not in table:
        return default
    return _positive(table, section, key, 1.0)


def _positive_int(table: dict[str, Any], section: str, key: str, default: int) -> int:
    value = _get(table, section, key, int, default)
    if value <= 0:
        raise ConfigError(f"{section}.{key} must be a positive integer")
    return value


def _score(table: dict[str, Any], section: str, key: str, default: int) -> int:
    value = _get(table, section, key, int, default)
    if not 0 <= value <= 100:
        raise ConfigError(f"{section}.{key} must be between 0 and 100")
    return value


def _choice(
    table: dict[str, Any], section: str, key: str, choices: tuple[str, ...], default: str
) -> str:
    value = _get(table, section, key, str, default)
    if value not in choices:
        raise ConfigError(f"{section}.{key} must be one of: {', '.join(choices)}")
    return value


def _path(table: dict[str, Any], section: str, key: str, default: Path) -> Path:
    value = _get(table, section, key, str, str(default))
    path = Path(value)
    if value and not path.is_absolute():
        raise ConfigError(f"{section}.{key} must be an absolute path")
    return path
