"""Identifier of a USB key: the signed token .pandora-box (airgap)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from pandora_box.keyid import (
    MAX_SIZE,
    TOKEN_FILE,
    TokenError,
    folder_of,
    generate_key,
    new_token,
    read_token,
    verify_token,
    write_token,
)


@pytest.fixture
def key(tmp_path: Path) -> Path:
    path = tmp_path / "token.key"
    generate_key(path)
    return path


def test_new_token_is_verified(key: Path, tmp_path: Path) -> None:
    identity, token = new_token(key)
    assert token.startswith(f"pandora-box-key 2 {identity.identifier} ".encode())
    # The name of the folder comes from the secret of the token, not from the
    # identifier: the identifier alone does not lead to the folder
    secret = token.split()[3]
    assert identity.folder == folder_of(base64.urlsafe_b64decode(secret + b"="))
    assert identity.identifier not in identity.folder and len(identity.folder) == 64
    assert len(token) < MAX_SIZE
    root = tmp_path / "key"
    root.mkdir()
    write_token(root, token)
    assert (root / TOKEN_FILE).read_bytes() == token
    assert not (root / f"{TOKEN_FILE}.tmp").exists()
    assert verify_token(read_token(root), key) == identity
    # Two keys never get the same identifier, nor the same folder
    other = new_token(key)[0]
    assert other.identifier != identity.identifier and other.folder != identity.folder


def test_token_of_another_organization(key: Path, tmp_path: Path) -> None:
    other = tmp_path / "other.key"
    generate_key(other)
    _, token = new_token(other)
    with pytest.raises(TokenError, match="not signed by the kiosks"):
        verify_token(token, key)


def test_forged_token(key: Path) -> None:
    identity, token = new_token(key)
    forged = token.replace(identity.identifier[:8].encode(), b"00000000")
    with pytest.raises(TokenError, match="not signed"):
        verify_token(forged, key)
    # Another secret (another folder) with the same signature
    parts = token.split()
    parts[3] = b"A" * 43
    with pytest.raises(TokenError, match="not signed"):
        verify_token(b" ".join(parts) + b"\n", key)


def test_token_of_the_first_version_is_replaced(key: Path) -> None:
    """A token without secret (version 1) is not one any more: a new one is written."""
    with pytest.raises(TokenError, match="malformed"):
        verify_token(
            b"pandora-box-key 1 12345678-1234-4234-8234-123456789abc " + b"A" * 86 + b"==\n", key
        )


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"hello",
        b"pandora-box-key 1 not-a-uuid AAAA\n",
        "pandora-box-key 1 é".encode(),
        b"pandora-box-key 2 12345678-1234-4234-8234-123456789abc "
        + b"B" * 43
        + b" "
        + b"A" * 86
        + b"==\nmore",
        b"\x00" * 100,
    ],
)
def test_malformed_token(key: Path, data: bytes) -> None:
    with pytest.raises(TokenError, match=r"malformed token|not signed"):
        verify_token(data, key)


def test_read_token_is_hostile_data(tmp_path: Path) -> None:
    with pytest.raises(TokenError, match="no token"):
        read_token(tmp_path)
    (tmp_path / TOKEN_FILE).write_bytes(b"x" * (MAX_SIZE + 1))
    with pytest.raises(TokenError, match="token of 513 bytes"):
        read_token(tmp_path)
    (tmp_path / TOKEN_FILE).unlink()
    (tmp_path / TOKEN_FILE).symlink_to("/etc/passwd")
    with pytest.raises(TokenError, match="cannot open"):
        read_token(tmp_path)
    (tmp_path / TOKEN_FILE).unlink()
    (tmp_path / TOKEN_FILE).mkdir()
    with pytest.raises(TokenError):
        read_token(tmp_path)


def test_write_token_never_follows_a_link(key: Path, tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.write_text("keep")
    root = tmp_path / "key"
    root.mkdir()
    (root / TOKEN_FILE).symlink_to(target)
    with pytest.raises(TokenError, match="is not a file"):
        write_token(root, new_token(key)[1])
    assert target.read_text() == "keep"
