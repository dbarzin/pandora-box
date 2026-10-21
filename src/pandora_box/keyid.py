"""Identifier of a USB key: a signed token in a hidden file at its root (airgap).

The serial number of a USB key is missing or shared on some keys: the kiosk
writes its own identifier, a random UUID, with a random secret, signed with
a key of the kiosks of the organization (Ed25519, systemd credential), in
.pandora-box at the root of the first filesystem of the key:

    pandora-box-key 2 <uuid> <secret> <base64 signature of the rest of the line>

The folder of the key on the server is named after the secret (folder_of):
the kiosk computes it when the key is inserted and never keeps it, so that
neither a stolen kiosk nor the account of the kiosks on the server, which
cannot list the folders, leads to the files of a key without the key.

The file comes from the key: it is read as hostile data (size limit, strict
format, signature checked before the identifier is used). A missing,
malformed or unsigned token is replaced with a new one (a new folder). The
signature prevents forging a token, not copying the file to another key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from pandora_box.sigsets import OPENSSL, SignatureSetError, verify_signature

TOKEN_FILE = ".pandora-box"  # noqa: S105  (a file name)
MAGIC = "pandora-box-key 2"
# A token is about 190 bytes: a bigger file is not one
MAX_SIZE = 512
SECRET_BYTES = 32
_TOKEN = re.compile(
    rf"^({MAGIC} ([0-9a-f]{{8}}-[0-9a-f]{{4}}-4[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}})"
    r" ([A-Za-z0-9_-]{43})) ([A-Za-z0-9+/]{86}==)\n?$"
)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
# Name of the folder of a key on the server
FOLDER = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class KeyIdentity:
    # In the scan report
    identifier: str
    # Name of the folder of the key on the server: never kept by the kiosk
    folder: str


def folder_of(secret: bytes) -> str:
    """The name of the folder of a key on the server, from the secret of its token."""
    return hashlib.sha256(b"pandora-box-folder 1 " + secret).hexdigest()


class TokenError(Exception):
    """The token of a key is missing, malformed or not signed by the kiosks."""


def read_token(root: Path) -> bytes:
    """The content of the token file of a mounted key; raise TokenError."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(root / TOKEN_FILE, flags)
    except FileNotFoundError:
        raise TokenError("no token") from None
    except OSError as ex:
        raise TokenError(f"cannot open the token: {ex.strerror}") from ex
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise TokenError("the token is not a regular file")
        if info.st_size > MAX_SIZE:
            raise TokenError(f"token of {info.st_size} bytes")
        data = os.read(fd, MAX_SIZE + 1)
    except OSError as ex:
        raise TokenError(f"cannot read the token: {ex.strerror}") from ex
    finally:
        os.close(fd)
    if len(data) > MAX_SIZE:
        raise TokenError("token too big")
    return data


def verify_token(data: bytes, private_key: Path) -> KeyIdentity:
    """The identity of a token signed with the key of the kiosks; raise TokenError."""
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise TokenError("malformed token") from None
    match = _TOKEN.match(text)
    if match is None:
        raise TokenError("malformed token")
    message, identifier, secret, signature = match[1], match[2], match[3], match[4]
    try:
        raw = base64.b64decode(signature, validate=True)
        secret_bytes = base64.urlsafe_b64decode(secret + "=")
    except (binascii.Error, ValueError):
        raise TokenError("malformed token") from None
    with tempfile.TemporaryDirectory(prefix="pandora-box-keyid-") as tmp:
        public = _public_key(private_key, Path(tmp))
        try:
            verify_signature(message.encode(), raw, [public])
        except SignatureSetError:
            raise TokenError("token not signed by the kiosks") from None
    return KeyIdentity(identifier, folder_of(secret_bytes))


def new_token(private_key: Path) -> tuple[KeyIdentity, bytes]:
    """A new identity and its token, signed with the key of the kiosks."""
    identifier = str(uuid.uuid4())
    secret = os.urandom(SECRET_BYTES)
    encoded_secret = base64.urlsafe_b64encode(secret).decode().rstrip("=")
    message = f"{MAGIC} {identifier} {encoded_secret}"
    with tempfile.TemporaryDirectory(prefix="pandora-box-keyid-") as tmp:
        data = Path(tmp, "message")
        data.write_text(message, encoding="ascii")
        signature = Path(tmp, "signature")
        _openssl(
            "pkeyutl", "-sign", "-inkey", str(private_key), "-rawin",
            "-in", str(data), "-out", str(signature),
        )  # fmt: skip
        encoded = base64.b64encode(signature.read_bytes()).decode()
    return KeyIdentity(identifier, folder_of(secret)), f"{message} {encoded}\n".encode()


def write_token(root: Path, token: bytes) -> None:
    """Write the token at the root of a key mounted read-write (replacing any file)."""
    path = root / TOKEN_FILE
    tmp = root / f"{TOKEN_FILE}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC
    # Not a link or a folder planted by the key
    for candidate in (path, tmp):
        if candidate.is_symlink() or candidate.is_dir():
            raise TokenError(f"{candidate.name} is not a file")
    fd = os.open(tmp, flags, 0o644)
    try:
        os.write(fd, token)
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.replace(path)


def generate_key(path: Path) -> None:
    """Create the private key of the kiosks of an organization (Ed25519)."""
    _openssl("genpkey", "-algorithm", "ed25519", "-out", str(path))
    path.chmod(0o600)


def _public_key(private_key: Path, folder: Path) -> Path:
    public = folder / "public.pem"
    _openssl("pkey", "-in", str(private_key), "-pubout", "-out", str(public))
    return public


def _openssl(*args: str) -> None:
    try:
        subprocess.run(  # noqa: S603  (fixed command, no shell)
            [OPENSSL, *args], check=True, capture_output=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as ex:
        raise TokenError(f"openssl {args[0]} failed: {ex}") from ex
