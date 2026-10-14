from __future__ import annotations

from pathlib import Path

import pytest

from pandora_box.config import (
    Config,
    ConfigError,
    LimitsConfig,
    YaraRuleSet,
    load_config,
    parse_config,
)

EXAMPLE = Path(__file__).parent.parent / "packaging" / "pandora-box.toml"


def test_defaults() -> None:
    config = parse_config({})
    assert config.kiosk.fake_scan is False
    assert config.kiosk.interface == "framebuffer"
    assert config.display.theme is None
    assert config.display.language == "en"
    assert config.device.mount_point == Path("/media/pandora-box")
    assert config.scan.workers == 0  # "auto"
    assert config.scan.suspicious == "block"
    assert config.scan.on_error == "block"
    assert config.limits.max_file_size == 1024**3
    assert config.policy.min_malicious_engines == 1
    assert config.quarantine.enabled is True
    engines = config.engines
    assert engines.malwarebazaar.enabled and engines.hashlookup.enabled
    assert engines.clamav.enabled and engines.yara.enabled
    # Known is not benign: content engines also scan known files by default
    assert engines.hashlookup.skip_content_engines is False
    assert engines.yara.on_compile_error == "fail"
    assert [r.name for r in engines.yara.rules] == ["yara-forge"]
    assert config.logging.level == "INFO"


def test_example_file_matches_defaults() -> None:
    config = load_config(EXAMPLE)
    defaults = Config()
    assert config.device == defaults.device
    assert config.scan == defaults.scan
    assert config.limits == defaults.limits
    assert config.policy == defaults.policy
    assert config.engines == defaults.engines
    assert config.signatures == defaults.signatures
    assert config.report == defaults.report
    assert config.quarantine == defaults.quarantine
    assert config.logging == defaults.logging
    assert config.siem == defaults.siem
    assert config.kiosk.fake_scan is False


def test_full_config() -> None:
    config = parse_config(
        {
            "kiosk": {"name": "kiosk-01", "fake_scan": True, "interface": "console"},
            "device": {
                "allowed_filesystems": ["vfat"],
                "use_sudo": True,
                "auto_mount": True,
                "eject": False,
                "auto_mount_wait": 5,
            },
            "scan": {"workers": 8, "suspicious": "warn", "on_error": "warn", "fake_delay": 1},
            "limits": {"max_file_size": 1000, "max_files": 10, "max_depth": 3},
            "policy": {"min_malicious_engines": 2},
            "report": {"folder": "/srv/reports"},
            "signatures": {"max_age_days": 2},
            "engines": {
                "malwarebazaar": {"enabled": False},
                "hashlookup": {"skip_content_engines": True, "max_age_days": 30},
                "clamav": {"socket": "/run/test/clamd.sock", "mode": "instream", "error_names": []},
                "yara": {
                    "rules": [
                        {"name": "forge", "path": "/srv/forge.yar"},
                        {"name": "sigbase", "path": "/srv/signature-base"},
                    ],
                    "cache_dir": "",
                    "on_compile_error": "skip_rule",
                    "exclude": ["*/thor*.yar"],
                    "malicious_score": 80,
                },
            },
            "logging": {"file": "", "level": "DEBUG"},
        }
    )
    assert config.scan.on_error == "warn"
    assert config.limits == LimitsConfig(1000, 10, 3)
    assert config.policy.min_malicious_engines == 2
    assert config.report.folder == Path("/srv/reports")
    assert config.signatures.max_age_days == 2.0
    assert config.engines.malwarebazaar.enabled is False
    assert config.engines.hashlookup.skip_content_engines is True
    assert config.engines.hashlookup.max_age_days == 30.0
    assert config.engines.clamav.mode == "instream"
    assert config.engines.clamav.error_names == ()
    yara = config.engines.yara
    assert yara.rules == (
        YaraRuleSet("forge", Path("/srv/forge.yar")),
        YaraRuleSet("sigbase", Path("/srv/signature-base")),
    )
    assert yara.cache_dir is None
    assert yara.on_compile_error == "skip_rule"
    assert yara.exclude == ("*/thor*.yar",)
    assert yara.malicious_score == 80
    assert config.kiosk.name == "kiosk-01"
    assert config.device.allowed_filesystems == ("vfat",)
    assert config.device.auto_mount is True
    assert config.device.eject is False
    assert config.device.auto_mount_wait == 5.0
    assert config.scan.fake_delay == 1.0
    assert config.scan.suspicious == "warn"
    assert config.logging.file is None


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"unknown": {}}, "unknown key"),
        ({"kiosk": {"fake_scan": "yes"}}, "kiosk.fake_scan must be of type bool"),
        ({"kiosk": {"interface": "web"}}, "kiosk.interface must be one of"),
        ({"kiosk": {"name": " "}}, "kiosk.name must not be empty"),
        ({"kiosk": {"typo": 1}}, "unknown key(s) in [kiosk]: typo"),
        ({"kiosk": 1}, "[kiosk] must be a table"),
        ({"device": {"mount_point": "media"}}, "must be an absolute path"),
        ({"device": {"allowed_filesystems": ["btrfs"]}}, "unsupported filesystem"),
        ({"device": {"allowed_filesystems": [1]}}, "list of strings"),
        ({"device": {"command_timeout": 0}}, "must be positive"),
        ({"device": {"auto_mount": "yes"}}, "device.auto_mount must be of type bool"),
        ({"device": {"auto_mount_wait": 0}}, "device.auto_mount_wait must be positive"),
        ({"scan": {"workers": 0}}, "between 1 and 64"),
        ({"scan": {"workers": True}}, "between 1 and 64"),
        ({"scan": {"workers": "many"}}, "between 1 and 64"),
        ({"scan": {"max_file_size": 1}}, "moved to limits.max_file_size"),
        ({"scan": {"on_error": "ignore"}}, "scan.on_error must be one of: block, warn"),
        ({"scan": {"file_timeout": 0}}, "scan.file_timeout must be positive"),
        ({"limits": {"max_file_size": -1}}, "limits.max_file_size must be a positive integer"),
        ({"limits": {"max_files": 0}}, "limits.max_files must be a positive integer"),
        ({"limits": {"max_depth": 1.5}}, "limits.max_depth must be of type int"),
        ({"policy": {"min_malicious_engines": 0}}, "must be a positive integer"),
        ({"report": {"folder": "reports"}}, "report.folder must be an absolute path"),
        ({"engines": {"av": {}}}, "unknown key(s) in [engines]: av"),
        ({"engines": {"clamav": 1}}, "[engines.clamav] must be a table"),
        ({"engines": {"clamav": {"mode": "tcp"}}}, "engines.clamav.mode must be one of"),
        ({"engines": {"clamav": {"socket": "clamd.ctl"}}}, "must be an absolute path"),
        ({"engines": {"clamav": {"suspicious_names": "PUA.*"}}}, "must be of type list"),
        ({"engines": {"hashlookup": {"max_age_days": 0}}}, "must be positive"),
        ({"engines": {"yara": {"rules": []}}}, "non-empty list of tables"),
        ({"engines": {"yara": {"rules": ["/a.yar"]}}}, "must be a table with name and path"),
        ({"engines": {"yara": {"rules": [{"name": "a"}]}}}, "needs a name and a path"),
        ({"engines": {"yara": {"rules": [{"name": "a b", "path": "/a"}]}}}, "letters, digits"),
        ({"engines": {"yara": {"rules": [{"name": "a", "path": ""}]}}}, "must not be empty"),
        (
            {
                "engines": {
                    "yara": {"rules": [{"name": "a", "path": "/a"}, {"name": "a", "path": "/b"}]}
                }
            },
            "duplicate name 'a'",
        ),
        ({"engines": {"yara": {"malicious_score": 101}}}, "between 0 and 100"),
        ({"engines": {"yara": {"suspicious_score": 90}}}, "must not exceed malicious_score"),
        ({"engines": {"yara": {"on_compile_error": "ignore"}}}, "on_compile_error must be one of"),
        ({"scan": {"fake_delay": -1}}, "must not be negative"),
        ({"scan": {"suspicious": "ignore"}}, "scan.suspicious must be one of: block, warn"),
        ({"logging": {"level": "TRACE"}}, "logging.level must be one of"),
    ],
)
def test_invalid(data: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=None) as info:
        parse_config(data)
    assert message in str(info.value)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml")


def test_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("[kiosk\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path)
