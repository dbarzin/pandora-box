"""Signed signature sets: format, verification and installation.

A signature set is a folder holding the signature files of the engines and a
signed list of them:

    manifest.json        the files of the set, with their SHA-256 and size
    manifest.json.sig    Ed25519 signature of manifest.json
    clamav/main.cvd      the files, by engine
    yara/...

    {"format": "pandora-box-signatures", "version": 1,
     "serial": 20261004120000, "created": "2026-10-04T12:00:00+00:00",
     "files": {"clamav/main.cvd": {"sha256": "...", "size": 123,
                                   "source": "...", "version": "...", "date": "..."}}}

The kiosk trusts the public keys of a folder of its image (signatures.keys,
protected by dm-verity). It installs a set only when its signature is valid,
its serial is higher than the installed one (no rollback) and every file has
the size and SHA-256 of the manifest. Installed sets live in:

    <signatures.folder>/sets/<serial>/
    <signatures.folder>/current -> sets/<serial>   (switched atomically)

The engines read their files through "current". The kiosk verifies the
installed set again (signature and every file) at each start.

A kiosk that downloads the signatures from their sources (updates.sources,
pandora_box.online) signs the set it builds with its own key, generated on
the kiosk, which it also trusts:

    <signatures.folder>/local-key/local.key   (root only)
    <signatures.folder>/local-key/local.pem

Signature sets are built and signed with the pandora-box-signatures command
(see main): signing uses openssl, so that the key can live in a hardware token
through an OpenSSL provider.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

FORMAT = "pandora-box-signatures"
VERSION = 1
# What a manifest lists: the signature set of the engines, or an update of the
# system image (pandora_box.imageupdate). A manifest of one kind is never
# accepted as the other.
CONTENT_SIGNATURES = "signatures"
CONTENT_IMAGE = "image"
MANIFEST = "manifest.json"
SIGNATURE = "manifest.json.sig"
# Folder holding a signature set at the root of an update device
UPDATE_FOLDER = "pandora-box-signatures"
CURRENT = "current"
SETS = "sets"
LOCAL_KEY = "local-key"

MAX_MANIFEST_SIZE = 16 * 1024 * 1024
MAX_SIGNATURE_SIZE = 4096
MAX_FILES = 10_000
MAX_DEPTH = 6
# Installed sets kept: the current one and the previous one
KEEP_SETS = 2
_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CHUNK = 1024 * 1024
# Absolute path: commands are never looked up in PATH
OPENSSL = "/usr/bin/openssl"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


class SignatureSetError(Exception):
    pass


class NotNewerError(SignatureSetError):
    """The set is not newer than the installed one."""


@dataclass(frozen=True)
class FileEntry:
    path: str  # relative POSIX path, validated
    sha256: str
    size: int
    source: str = ""
    version: str = ""
    date: str = ""


@dataclass(frozen=True)
class Manifest:
    serial: int
    created: datetime
    files: tuple[FileEntry, ...]
    content: str = CONTENT_SIGNATURES

    def entry(self, path: str) -> FileEntry | None:
        return next((f for f in self.files if f.path == path), None)

    def to_json(self) -> bytes:
        files = {}
        for f in self.files:
            item: dict[str, object] = {"sha256": f.sha256, "size": f.size}
            item.update({k: v for k, v in (("source", f.source), ("version", f.version),
                                           ("date", f.date)) if v})  # fmt: skip
            files[f.path] = item
        data = {
            "format": FORMAT,
            "version": VERSION,
            "content": self.content,
            "serial": self.serial,
            "created": self.created.isoformat(),
            "files": files,
        }
        return (json.dumps(data, indent=1, sort_keys=True) + "\n").encode()

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


def parse_manifest(data: bytes, content: str = CONTENT_SIGNATURES) -> Manifest:
    """Validate a manifest of that content (its signature must have been verified first)."""
    try:
        raw = json.loads(data)
    except ValueError as ex:
        raise SignatureSetError(f"invalid manifest: {ex}") from ex
    if not isinstance(raw, dict) or raw.get("format") != FORMAT or raw.get("version") != VERSION:
        raise SignatureSetError("not a signature set manifest (format, version)")
    if raw.get("content", CONTENT_SIGNATURES) != content:
        raise SignatureSetError(f"not a manifest of {content}: {str(raw.get('content'))[:40]}")
    serial = raw.get("serial")
    if isinstance(serial, bool) or not isinstance(serial, int) or serial <= 0:
        raise SignatureSetError("invalid manifest serial")
    try:
        created = datetime.fromisoformat(str(raw.get("created")))
    except ValueError as ex:
        raise SignatureSetError("invalid manifest creation date") from ex
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    files = raw.get("files")
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise SignatureSetError("invalid manifest file list")
    entries = []
    for path, item in files.items():
        if not isinstance(item, dict):
            raise SignatureSetError(f"invalid manifest entry: {path!r}")
        sha256, size = item.get("sha256"), item.get("size")
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            raise SignatureSetError(f"invalid SHA-256 for {path!r}")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise SignatureSetError(f"invalid size for {path!r}")
        meta = {k: item.get(k, "") for k in ("source", "version", "date")}
        if not all(isinstance(v, str) and len(v) <= 1024 for v in meta.values()):
            raise SignatureSetError(f"invalid metadata for {path!r}")
        entries.append(FileEntry(validate_path(path), sha256, size, **meta))
    return Manifest(serial, created, tuple(sorted(entries, key=lambda e: e.path)), content)


def validate_path(path: object) -> str:
    """A relative path of plain names: no "..", no hidden name, no manifest."""
    if not isinstance(path, str):
        raise SignatureSetError("invalid path in manifest")
    parts = path.split("/")
    if not 2 <= len(parts) <= MAX_DEPTH or not all(_COMPONENT.fullmatch(p) for p in parts):
        raise SignatureSetError(f"invalid path in manifest: {path[:200]!r}")
    return path


# -- signatures ------------------------------------------------------------------------


def local_public_keys(folder: Path) -> list[Path]:
    """The public key of the kiosk itself, when it has one (signatures.folder)."""
    path = folder / LOCAL_KEY / "local.pem"
    return [path] if path.is_file() else []


def local_key(folder: Path) -> Path:
    """The private key of the kiosk (signatures.folder), created when missing."""
    directory = folder / LOCAL_KEY
    key, public = directory / "local.key", directory / "local.pem"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o755)
    if not key.exists():
        partial = directory / ".local.key"
        partial.unlink(missing_ok=True)
        subprocess.run(  # noqa: S603  (fixed command, no shell)
            [OPENSSL, "genpkey", "-algorithm", "ed25519", "-out", str(partial)],
            check=True, capture_output=True, timeout=60,
        )  # fmt: skip
        partial.chmod(0o600)
        partial.rename(key)
    if not public.exists():
        subprocess.run(  # noqa: S603  (fixed command, no shell)
            [OPENSSL, "pkey", "-in", str(key), "-pubout", "-out", str(public)],
            check=True, capture_output=True, timeout=60,
        )  # fmt: skip
        public.chmod(0o644)
    return key


def trusted_keys(folder: Path) -> list[Path]:
    """The public keys (PEM) of a folder."""
    try:
        return sorted(p for p in folder.iterdir() if p.suffix == ".pem" and p.is_file())
    except OSError:
        return []


def verify_signature(manifest: bytes, signature: bytes, keys: Sequence[Path]) -> Path:
    """Return the key that signed the manifest; raise SignatureSetError if none did."""
    if not keys:
        raise SignatureSetError("no trusted signature key")
    with tempfile.TemporaryDirectory(prefix="pandora-box-sig-") as tmp:
        data, sig = Path(tmp, "manifest"), Path(tmp, "signature")
        data.write_bytes(manifest)
        sig.write_bytes(signature)
        for key in keys:
            result = subprocess.run(  # noqa: S603  (fixed command, no shell)
                [OPENSSL, "pkeyutl", "-verify", "-pubin", "-inkey", str(key),
                 "-rawin", "-in", str(data), "-sigfile", str(sig)],
                capture_output=True, check=False, timeout=30,
            )  # fmt: skip
            if result.returncode == 0:
                return key
    raise SignatureSetError("invalid signature: not signed by a trusted key")


def sign(folder: Path, key: Path) -> None:
    """Sign the manifest of a set with a private key (openssl)."""
    subprocess.run(  # noqa: S603  (fixed command, no shell)
        [OPENSSL, "pkeyutl", "-sign", "-inkey", str(key), "-rawin",
         "-in", str(folder / MANIFEST), "-out", str(folder / SIGNATURE)],
        check=True, capture_output=True, timeout=60,
    )  # fmt: skip


# -- reading a set safely -------------------------------------------------------------


@contextlib.contextmanager
def _open_dir(path: Path) -> Iterator[int]:
    fd = os.open(path, _DIR_FLAGS)
    try:
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def _open_file(dir_fd: int, relpath: str) -> Iterator[int]:
    """Open a regular file below dir_fd, never following a link."""
    parts = relpath.split("/")
    current = os.dup(dir_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, _DIR_FLAGS, dir_fd=current)
            os.close(current)
            current = next_fd
        fd = os.open(parts[-1], _FILE_FLAGS, dir_fd=current)
    finally:
        os.close(current)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SignatureSetError(f"not a regular file: {relpath}")
        yield fd
    finally:
        os.close(fd)


def _read_small(dir_fd: int, name: str, limit: int) -> bytes:
    data = b""
    try:
        with _open_file(dir_fd, name) as fd:
            while len(data) <= limit and (chunk := os.read(fd, limit + 1 - len(data))):
                data += chunk
    except OSError as ex:
        raise SignatureSetError(f"cannot read {name}: {ex.strerror}") from ex
    if len(data) > limit:
        raise SignatureSetError(f"{name} is too big")
    return data


def read_set(
    folder: Path, keys: Sequence[Path], content: str = CONTENT_SIGNATURES
) -> tuple[Manifest, bytes, bytes]:
    """Read and verify the manifest of a set: (manifest, its bytes, signature)."""
    try:
        with _open_dir(folder) as dir_fd:
            data = _read_small(dir_fd, MANIFEST, MAX_MANIFEST_SIZE)
            signature = _read_small(dir_fd, SIGNATURE, MAX_SIGNATURE_SIZE)
    except OSError as ex:
        raise SignatureSetError(f"cannot open the signature set: {ex.strerror}") from ex
    # The exact bytes that were verified are parsed
    verify_signature(data, signature, keys)
    return parse_manifest(data, content), data, signature


def _copy_verified(src_fd: int, entry: FileEntry, target: Path) -> None:
    digest = hashlib.sha256()
    size = 0
    with target.open("xb") as out:
        while chunk := os.read(src_fd, _CHUNK):
            size += len(chunk)
            if size > entry.size:
                raise SignatureSetError(f"{entry.path}: bigger than in the manifest")
            digest.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    if size != entry.size or digest.hexdigest() != entry.sha256:
        raise SignatureSetError(f"{entry.path}: content does not match the manifest")
    target.chmod(0o644)


def _make_parents(root: Path, relpath: str) -> Path:
    """The target path of a file of a set, with its folders (0755) created."""
    target = root / relpath
    for parent in reversed(target.relative_to(root).parents[:-1]):
        if not (root / parent).is_dir():
            (root / parent).mkdir()
            (root / parent).chmod(0o755)
    return target


def _copy_entry(source_fd: int, entry: FileEntry, target: Path) -> None:
    try:
        with _open_file(source_fd, entry.path) as fd:
            _copy_verified(fd, entry, target)
    except OSError as ex:
        raise SignatureSetError(f"cannot read {entry.path}: {ex.strerror}") from ex


def copy_files(source: Path, manifest: Manifest, target: Path) -> None:
    """Copy the files of a verified manifest from source, each checked."""
    if shutil.disk_usage(target).free < manifest.total_size:
        raise SignatureSetError("not enough free space")
    try:
        with _open_dir(source) as source_fd:
            for entry in manifest.files:
                _copy_entry(source_fd, entry, _make_parents(target, entry.path))
    except OSError as ex:
        raise SignatureSetError(f"cannot read the files: {ex.strerror}") from ex


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


# -- installed sets --------------------------------------------------------------------


def current_path(folder: Path) -> Path:
    return folder / CURRENT


def current_set(folder: Path) -> Path | None:
    """The folder of the installed set (the target of "current"), or None."""
    try:
        target = (folder / CURRENT).readlink()
    except OSError:
        return None
    if len(target.parts) != 2 or target.parts[0] != SETS or not target.parts[1].isdigit():
        return None
    return folder / target


def installed_manifest(folder: Path) -> Manifest | None:
    """The manifest of the installed set (not verified), or None."""
    current = current_set(folder)
    try:
        return None if current is None else parse_manifest((current / MANIFEST).read_bytes())
    except (OSError, SignatureSetError):
        return None


def verify_installed(folder: Path, keys: Sequence[Path]) -> Manifest:
    """Verify the installed set: signature, then the size and SHA-256 of every file."""
    current = current_set(folder)
    if current is None or not current.is_dir():
        raise SignatureSetError(f"no signature set installed in {folder}")
    manifest, _, _ = read_set(current, keys)
    for entry in manifest.files:
        path = current / entry.path
        if path.is_symlink() or not path.is_file():
            raise SignatureSetError(f"installed signature file missing: {entry.path}")
        if (entry.sha256, entry.size) != _hash_file(path):
            raise SignatureSetError(f"installed signature file modified: {entry.path}")
    return manifest


def install(source: Path, folder: Path, keys: Sequence[Path]) -> Manifest:
    """Install a signature set; return its manifest.

    Raise NotNewerError when its serial is not higher than the installed one,
    SignatureSetError when it is not valid. Nothing is changed on error.
    """
    manifest, data, signature = read_set(source, keys)
    installed = installed_manifest(folder)
    installed_folder = current_set(folder)
    if installed is not None and manifest.serial <= installed.serial:
        raise NotNewerError(
            f"serial {manifest.serial} is not newer than the installed {installed.serial}"
        )
    sets = folder / SETS
    sets.mkdir(parents=True, exist_ok=True)
    for path in (folder, sets):
        path.chmod(0o755)
    final = sets / str(manifest.serial)
    staging = sets / f".staging-{manifest.serial}"
    shutil.rmtree(staging, ignore_errors=True)
    # Explicit modes: readable by clamd and the scan workers whatever the umask
    staging.mkdir()
    staging.chmod(0o755)
    try:
        if shutil.disk_usage(sets).free < manifest.total_size:
            raise SignatureSetError("not enough free space for the signature set")
        with _open_dir(source) as source_fd:
            for entry in manifest.files:
                target = _make_parents(staging, entry.path)
                old = None if installed is None else installed.entry(entry.path)
                if (
                    old is not None
                    and installed_folder is not None
                    and (old.sha256, old.size) == (entry.sha256, entry.size)
                ):
                    # Unchanged file: shared with the installed set (sets are never modified)
                    os.link(installed_folder / entry.path, target)
                    continue
                _copy_entry(source_fd, entry, target)
        for name, content in ((MANIFEST, data), (SIGNATURE, signature)):
            (staging / name).write_bytes(content)
            (staging / name).chmod(0o644)
        shutil.rmtree(final, ignore_errors=True)
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    # Atomic switch of the current set
    link = folder / f".{CURRENT}.new"
    link.unlink(missing_ok=True)
    link.symlink_to(Path(SETS) / str(manifest.serial))
    link.rename(current_path(folder))
    _remove_old_sets(sets, manifest.serial)
    return manifest


def _remove_old_sets(sets: Path, current: int) -> None:
    serials = sorted(int(p.name) for p in sets.iterdir() if p.name.isdigit())
    keep = {s for s in serials if s <= current}
    keep = set(sorted(keep)[-KEEP_SETS:])
    for serial in serials:
        if serial not in keep:
            shutil.rmtree(sets / str(serial), ignore_errors=True)


# -- building a set (publisher side) ---------------------------------------------------


def build(
    folder: Path,
    serial: int | None = None,
    created: datetime | None = None,
    content: str = CONTENT_SIGNATURES,
    only: Sequence[str] | None = None,
) -> Manifest:
    """Write the manifest of the files of folder (to be signed with sign()).

    The source, version and date of a file come from the manifest.json of its
    folder when there is one (pandora_box.publish writes them). only: these
    files of the folder, not all of them.
    """
    created = created or datetime.now(UTC).replace(microsecond=0)
    serial = serial or int(created.strftime("%Y%m%d%H%M%S"))
    entries = []
    paths = sorted(folder.rglob("*")) if only is None else [folder / p for p in sorted(only)]
    for path in paths:
        rel = path.relative_to(folder).as_posix()
        if rel in (MANIFEST, SIGNATURE) or (path.is_dir() and not path.is_symlink()):
            continue
        if path.is_symlink() or not path.is_file():
            raise SignatureSetError(f"not a regular file: {rel}")
        sha256, size = _hash_file(path)
        meta = _folder_metadata(path)
        entries.append(FileEntry(validate_path(rel), sha256, size, **meta))
    manifest = Manifest(serial, created, tuple(entries), content)
    parse_manifest(manifest.to_json(), content)  # same checks as the kiosk
    (folder / MANIFEST).write_bytes(manifest.to_json())
    return manifest


def _folder_metadata(path: Path) -> dict[str, str]:
    try:
        data = json.loads((path.parent / MANIFEST).read_text())
        item = data.get(path.name, {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        item = {}
    if not isinstance(item, dict):
        return {}
    return {k: str(item[k]) for k in ("source", "version", "date") if item.get(k)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pandora-box-signatures", description="Build, sign, verify and install signature sets"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_publish = sub.add_parser(
        "publish", help="download the signatures, check them, then build and sign a set"
    )
    p_publish.add_argument("output", type=Path, help="folder of the set (replaced)")
    p_publish.add_argument(
        "--key", type=Path, help="private key (PEM) to sign the set; none: development set"
    )
    p_publish.add_argument(
        "--sources",
        default="clamav,yara-forge,malwarebazaar,hashlookup",
        help="comma-separated: clamav, yara-forge, signature-base, malwarebazaar, hashlookup",
    )
    p_publish.add_argument(
        "--cache", type=Path, default=Path.home() / ".cache" / "pandora-box-signatures"
    )
    p_publish.add_argument("--serial", type=int, help="default: the date, YYYYMMDDHHMMSS")
    p_publish.add_argument(
        "--abusech-key-file", type=Path, help="abuse.ch Auth-Key (default: $ABUSECH_AUTH_KEY)"
    )
    p_build = sub.add_parser("build", help="write the manifest of a folder, and sign it")
    p_build.add_argument("folder", type=Path)
    p_build.add_argument("--serial", type=int, help="default: the date, YYYYMMDDHHMMSS")
    p_build.add_argument("--key", type=Path, help="private key (PEM) to sign the manifest")
    p_sign = sub.add_parser("sign", help="sign the manifest of a set")
    p_sign.add_argument("folder", type=Path)
    p_sign.add_argument("--key", type=Path, required=True)
    for name, help_text in (("verify", "verify a set"), ("install", "install a set")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("folder", type=Path)
        p.add_argument("--keys", type=Path, default=Path("/usr/share/pandora-box/keys"))
        if name == "install":
            p.add_argument("--target", type=Path, default=Path("/var/lib/pandora-box-signatures"))
            p.add_argument(
                "--staged",
                action="store_true",
                help="a set staged by download: nothing staged or not newer is not an error,"
                " and the staged files are removed",
            )
            p.add_argument(
                "--config",
                type=Path,
                default=Path("/etc/pandora-box/pandora-box.toml"),
                help="with --staged: a kiosk that downloads the signatures from their"
                " sources signs the staged set with its own key",
            )
    p_maintenance = sub.add_parser(
        "maintenance",
        help="write a signed maintenance request: the kiosk exports its logs to the device",
    )
    p_maintenance.add_argument(
        "folder", type=Path, help="pandora-box-maintenance folder, at the root of a USB key"
    )
    p_maintenance.add_argument("--key", type=Path, required=True, help="update key (PEM)")
    p_maintenance.add_argument("--kiosk", default="", help="only this kiosk (kiosk.name)")
    p_download = sub.add_parser(
        "download",
        help="build a set from the sources (updates.sources), or download the published"
        " set (updates.url), when it is newer",
    )
    p_download.add_argument("--staging", type=Path, required=True)
    p_download.add_argument(
        "--config", type=Path, default=Path("/etc/pandora-box/pandora-box.toml")
    )
    args = parser.parse_args(argv)
    if args.command == "publish":
        return _publish(args)
    if args.command == "download":
        return _download(args)
    if args.command == "maintenance":
        from pandora_box import maintenance

        try:
            request = maintenance.request(args.folder, args.key, args.kiosk)
        except (SignatureSetError, OSError, subprocess.CalledProcessError) as ex:
            print(f"pandora-box-signatures: {ex}", file=sys.stderr)
            return 1
        print(
            f"maintenance request of {request.created:%Y-%m-%d} in {args.folder}: valid "
            f"{maintenance.VALID_DAYS} days, for {args.kiosk or 'any kiosk'}"
        )
        return 0
    if args.command == "install" and args.staged:
        return _install_staged(args)
    try:
        if args.command == "build":
            manifest = build(args.folder, args.serial)
            if args.key:
                sign(args.folder, args.key)
            print(f"serial {manifest.serial}: {len(manifest.files)} files, "
                  f"{manifest.total_size} bytes{', signed' if args.key else ''}")  # fmt: skip
        elif args.command == "sign":
            sign(args.folder, args.key)
        elif args.command == "verify":
            keys = trusted_keys(args.keys)
            manifest, _, _ = read_set(args.folder, keys)
            for entry in manifest.files:
                if (entry.sha256, entry.size) != _hash_file(args.folder / entry.path):
                    raise SignatureSetError(f"{entry.path}: content does not match")
            print(f"serial {manifest.serial}: valid, {len(manifest.files)} files")
        else:
            manifest = install(args.folder, args.target, trusted_keys(args.keys))
            print(f"serial {manifest.serial} installed in {args.target}")
    except (SignatureSetError, OSError, subprocess.CalledProcessError) as ex:
        print(f"pandora-box-signatures: {ex}", file=sys.stderr)
        return 1
    return 0


def _download(args: argparse.Namespace) -> int:
    from pandora_box.config import ConfigError, load_config
    from pandora_box.online import UpdateError, download

    try:
        manifest = download(load_config(args.config), args.staging)
    except (ConfigError, UpdateError, OSError) as ex:
        print(f"pandora-box-signatures: {ex}", file=sys.stderr)
        return 1
    if manifest is not None:
        print(f"set {manifest.serial} staged in {args.staging}")
    return 0


def _builds_from_sources(config_path: Path) -> bool:
    """The kiosk downloads the signatures from their sources (updates.sources)."""
    from pandora_box.config import ConfigError, load_config

    if not config_path.exists():
        return False
    try:
        updates = load_config(config_path).updates
    except ConfigError:
        return False
    return updates.enabled and bool(updates.sources)


def _install_staged(args: argparse.Namespace) -> int:
    if not (args.folder / MANIFEST).exists():
        print("no signature set staged")
        return 0
    status = 0
    keys = trusted_keys(args.keys)
    try:
        if _builds_from_sources(args.config):
            # Built by the update service of this kiosk: signed with its own key
            sign(args.folder, local_key(args.target))
            keys += local_public_keys(args.target)
        manifest = install(args.folder, args.target, keys)
        print(f"serial {manifest.serial} installed in {args.target}")
    except NotNewerError as ex:
        print(f"not installed: {ex}")
    except (SignatureSetError, OSError, subprocess.CalledProcessError) as ex:
        print(f"pandora-box-signatures: {ex}", file=sys.stderr)
        status = 1
    for path in args.folder.iterdir():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    return status


def _publish(args: argparse.Namespace) -> int:
    from pandora_box.publish import PublishError, development_config, publish, read_auth_key

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    try:
        manifest = publish(
            args.output.resolve(),
            sources,
            args.cache,
            args.key,
            args.serial,
            read_auth_key(args.abusech_key_file),
        )
    except (PublishError, SignatureSetError, OSError, subprocess.CalledProcessError) as ex:
        print(f"pandora-box-signatures: {ex}", file=sys.stderr)
        return 1
    print(f"serial {manifest.serial}: {len(manifest.files)} files, {manifest.total_size} bytes")
    if args.key is None:
        print("\nNot signed: for development only. Configuration (pandora-box.toml):\n")
        print(development_config(args.output.resolve(), sources))
    return 0


if __name__ == "__main__":
    sys.exit(main())
