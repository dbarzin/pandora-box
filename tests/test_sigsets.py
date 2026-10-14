from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pandora_box.sigsets import (
    MANIFEST,
    SIGNATURE,
    NotNewerError,
    SignatureSetError,
    build,
    install,
    installed_manifest,
    main,
    sign,
    trusted_keys,
    verify_installed,
)

pytestmark = pytest.mark.skipif(not Path("/usr/bin/openssl").exists(), reason="no openssl")


def make_key(folder: Path, name: str) -> Path:
    """An Ed25519 key pair: name.key (private), name.pem (public)."""
    folder.mkdir(parents=True, exist_ok=True)
    private = folder / f"{name}.key"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(private)], check=True
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(folder / f"{name}.pem")],
        check=True,
    )
    return private


@pytest.fixture
def key(tmp_path: Path) -> Path:
    return make_key(tmp_path / "keys", "update")


@pytest.fixture
def keys(key: Path) -> list[Path]:
    return trusted_keys(key.parent)


def make_set(folder: Path, files: dict[str, bytes], serial: int, key: Path) -> Path:
    for name, content in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(content)
    build(folder, serial)
    sign(folder, key)
    return folder


FILES = {"clamav/test.hdb": b"clamav db\n", "yara/rules/test.yar": b"rule x { condition: true }"}


def test_install(tmp_path: Path, key: Path, keys: list[Path]) -> None:
    source = make_set(tmp_path / "set", FILES, 1, key)
    target = tmp_path / "installed"
    manifest = install(source, target, keys)
    assert manifest.serial == 1
    current = target / "current"
    assert current.is_symlink()
    assert current.readlink() == Path("sets/1")
    assert (current / "clamav/test.hdb").read_bytes() == b"clamav db\n"
    assert (current / "yara/rules/test.yar").stat().st_mode & 0o777 == 0o644
    assert (target / "sets").stat().st_mode & 0o777 == 0o755
    assert verify_installed(target, keys).serial == 1
    assert installed_manifest(target) == manifest


def test_update_shares_unchanged_files(tmp_path: Path, key: Path, keys: list[Path]) -> None:
    target = tmp_path / "installed"
    install(make_set(tmp_path / "s1", FILES, 1, key), target, keys)
    changed = FILES | {"yara/rules/test.yar": b"rule y { condition: true }"}
    install(make_set(tmp_path / "s2", changed, 2, key), target, keys)
    old, new = target / "sets/1", target / "sets/2"
    assert (old / "clamav/test.hdb").stat().st_ino == (new / "clamav/test.hdb").stat().st_ino
    assert (new / "yara/rules/test.yar").read_bytes() == b"rule y { condition: true }"
    assert (old / "yara/rules/test.yar").read_bytes() == b"rule x { condition: true }"
    # The current and the previous sets are kept
    install(make_set(tmp_path / "s3", FILES, 3, key), target, keys)
    assert sorted(p.name for p in (target / "sets").iterdir()) == ["2", "3"]
    assert verify_installed(target, keys).serial == 3


@pytest.mark.parametrize("serial", [1, 2])
def test_no_rollback(tmp_path: Path, key: Path, keys: list[Path], serial: int) -> None:
    target = tmp_path / "installed"
    install(make_set(tmp_path / "s2", FILES, 2, key), target, keys)
    with pytest.raises(NotNewerError):
        install(make_set(tmp_path / "old", FILES, serial, key), target, keys)
    assert installed_manifest(target).serial == 2  # type: ignore[union-attr]


def assert_refused(source: Path, target: Path, keys: list[Path], message: str) -> None:
    with pytest.raises(SignatureSetError, match=message):
        install(source, target, keys)
    assert not (target / "current").exists()
    assert not any(p.name.startswith(".staging") for p in target.glob("sets/*"))


def test_foreign_key_is_refused(tmp_path: Path, keys: list[Path]) -> None:
    foreign = make_key(tmp_path / "foreign", "foreign")
    source = make_set(tmp_path / "set", FILES, 1, foreign)
    assert_refused(source, tmp_path / "installed", keys, "invalid signature")


def test_modified_file_is_refused(tmp_path: Path, key: Path, keys: list[Path]) -> None:
    source = make_set(tmp_path / "set", FILES, 1, key)
    (source / "clamav/test.hdb").write_bytes(b"clamav dB\n")
    assert_refused(source, tmp_path / "installed", keys, "does not match the manifest")


def test_modified_manifest_is_refused(tmp_path: Path, key: Path, keys: list[Path]) -> None:
    source = make_set(tmp_path / "set", FILES, 1, key)
    data = json.loads((source / MANIFEST).read_text())
    data["serial"] = 99
    (source / MANIFEST).write_text(json.dumps(data))
    assert_refused(source, tmp_path / "installed", keys, "invalid signature")


def test_missing_signature_is_refused(tmp_path: Path, key: Path, keys: list[Path]) -> None:
    source = make_set(tmp_path / "set", FILES, 1, key)
    (source / SIGNATURE).unlink()
    assert_refused(source, tmp_path / "installed", keys, "cannot read manifest.json.sig")


@pytest.mark.parametrize(
    "path", ["../etc/passwd", "/etc/passwd", "clamav/../../x", "clamav/.hidden", "top-level.db"]
)
def test_unsafe_paths_are_refused(tmp_path: Path, key: Path, keys: list[Path], path: str) -> None:
    source = make_set(tmp_path / "set", FILES, 1, key)
    data = json.loads((source / MANIFEST).read_text())
    data["files"][path] = data["files"]["clamav/test.hdb"]
    (source / MANIFEST).write_text(json.dumps(data))
    sign(source, key)
    assert_refused(source, tmp_path / "installed", keys, "invalid path in manifest")


def test_symlinks_are_not_followed(tmp_path: Path, key: Path, keys: list[Path]) -> None:
    secret = tmp_path / "secret"
    secret.write_bytes(b"clamav db\n")  # same content as the signed file
    source = make_set(tmp_path / "set", FILES, 1, key)
    (source / "clamav/test.hdb").unlink()
    (source / "clamav/test.hdb").symlink_to(secret)
    assert_refused(source, tmp_path / "installed", keys, "cannot read clamav/test.hdb")
    shutil.rmtree(source / "clamav")
    (source / "clamav").symlink_to(secret.parent)
    assert_refused(source, tmp_path / "installed", keys, "cannot read clamav/test.hdb")


def test_unlisted_files_are_not_installed(tmp_path: Path, key: Path, keys: list[Path]) -> None:
    source = make_set(tmp_path / "set", FILES, 1, key)
    (source / "clamav/extra.ndb").write_bytes(b"not signed")
    install(source, tmp_path / "installed", keys)
    assert not (tmp_path / "installed/current/clamav/extra.ndb").exists()


def test_modified_installed_set_is_detected(tmp_path: Path, key: Path, keys: list[Path]) -> None:
    target = tmp_path / "installed"
    install(make_set(tmp_path / "set", FILES, 1, key), target, keys)
    (target / "current/clamav/test.hdb").write_bytes(b"emptied")
    with pytest.raises(SignatureSetError, match=r"modified: clamav/test\.hdb"):
        verify_installed(target, keys)
    with pytest.raises(SignatureSetError, match="no signature set installed"):
        verify_installed(tmp_path / "nothing", keys)


def test_build_metadata(tmp_path: Path, key: Path) -> None:
    folder = tmp_path / "set"
    (folder / "yara").mkdir(parents=True)
    (folder / "yara/core.yar").write_text("rule a { condition: true }")
    (folder / "yara/manifest.json").write_text(
        json.dumps({"core.yar": {"version": "20261001", "source": "https://example.org/"}})
    )
    manifest = build(folder, 7)
    entry = manifest.entry("yara/core.yar")
    assert entry is not None
    assert (entry.version, entry.source) == ("20261001", "https://example.org/")


def test_command_line(tmp_path: Path, key: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "set"
    for name, content in FILES.items():
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_bytes(content)
    assert main(["build", str(source), "--serial", "5", "--key", str(key)]) == 0
    assert main(["verify", str(source), "--keys", str(key.parent)]) == 0
    target = tmp_path / "installed"
    assert main(["install", str(source), "--keys", str(key.parent), "--target", str(target)]) == 0
    assert main(["install", str(source), "--keys", str(key.parent), "--target", str(target)]) == 1
    assert "not newer" in capsys.readouterr().err


def test_install_modes_do_not_depend_on_the_umask(
    tmp_path: Path, key: Path, keys: list[Path]
) -> None:
    """clamd and the scan workers read the set: the update service has umask 077."""
    source = make_set(tmp_path / "set", FILES, 1, key)
    old = os.umask(0o077)
    try:
        install(source, tmp_path / "installed", keys)
    finally:
        os.umask(old)
    current = tmp_path / "installed/current"
    for path in [current, *current.rglob("*")]:
        expected = 0o755 if path.is_dir() else 0o644
        assert path.stat().st_mode & 0o777 == expected, path
