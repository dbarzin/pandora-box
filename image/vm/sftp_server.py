# paramiko has no type hints: its classes are Any
# mypy: disable-error-code="misc,no-any-return"
"""SFTP server of the virtual machine test (copy of the verified keys, airgap).

The kiosk of the test image copies the clean files of a key to it, over the
QEMU user network (10.0.2.2 in the machine). It serves one folder, accepts
only the client key of image/vm/airgap/ and presents the host key that the
test image trusts (both made by image/build-test.sh).
"""

from __future__ import annotations

import os
import socket
import threading
from pathlib import Path
from typing import Any

import paramiko
from paramiko.sftp import SFTP_FAILURE, SFTP_NO_SUCH_FILE, SFTP_OK, SFTP_PERMISSION_DENIED


class _Server(paramiko.ServerInterface):
    def __init__(self, authorized: str) -> None:
        self.authorized = authorized

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        if key.get_base64() == self.authorized:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "publickey"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


def _errno(ex: OSError) -> int:
    return paramiko.SFTPServer.convert_errno(ex.errno or 0)


class _Folder(paramiko.SFTPServerInterface):
    """The SFTP operations, in the served folder (never out of it)."""

    root = Path("/nonexistent")

    def _real(self, path: str) -> Path:
        resolved = (self.root / path.lstrip("/")).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise PermissionError(path)
        return resolved

    def _attributes(self, path: Path) -> paramiko.SFTPAttributes:
        attributes = paramiko.SFTPAttributes.from_stat(path.lstat())
        attributes.filename = path.name
        return attributes

    def list_folder(self, path: str) -> Any:
        try:
            return [self._attributes(p) for p in self._real(path).iterdir()]
        except OSError as ex:
            return _errno(ex)

    def stat(self, path: str) -> Any:
        try:
            return paramiko.SFTPAttributes.from_stat(self._real(path).stat())
        except OSError as ex:
            return _errno(ex)

    lstat = stat

    def open(self, path: str, flags: int, attr: paramiko.SFTPAttributes) -> Any:
        try:
            fd = os.open(self._real(path), flags | getattr(os, "O_BINARY", 0), 0o644)
        except OSError as ex:
            return _errno(ex)
        mode = "wb" if flags & os.O_WRONLY else "r+b" if flags & os.O_RDWR else "rb"
        handle = paramiko.SFTPHandle(flags)
        stream = os.fdopen(fd, mode)
        handle.readfile = stream
        handle.writefile = stream
        return handle

    def remove(self, path: str) -> int:
        return self._call(lambda: self._real(path).unlink())

    def rename(self, oldpath: str, newpath: str) -> int:
        return self._call(lambda: self._real(oldpath).rename(self._real(newpath)))

    def posix_rename(self, oldpath: str, newpath: str) -> int:
        return self._call(lambda: self._real(oldpath).replace(self._real(newpath)))

    def mkdir(self, path: str, attr: paramiko.SFTPAttributes) -> int:
        return self._call(lambda: self._real(path).mkdir())

    def rmdir(self, path: str) -> int:
        return self._call(lambda: self._real(path).rmdir())

    def chattr(self, path: str, attr: paramiko.SFTPAttributes) -> int:
        if attr.st_atime is not None and attr.st_mtime is not None:
            return self._call(lambda: os.utime(self._real(path), (attr.st_atime, attr.st_mtime)))
        return SFTP_OK

    def _call(self, action: Any) -> int:
        try:
            action()
        except PermissionError:
            return SFTP_PERMISSION_DENIED
        except FileNotFoundError:
            return SFTP_NO_SUCH_FILE
        except OSError:
            return SFTP_FAILURE
        return SFTP_OK


class SftpServer:
    """Serve root on 127.0.0.1:port, in threads, until the test ends."""

    def __init__(self, root: Path, port: int, host_key: Path, client_key: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        _Folder.root = root.resolve()
        self.host_key = paramiko.Ed25519Key.from_private_key_file(str(host_key))
        self.authorized = client_key.read_text().split()[1]
        self.listener = socket.create_server(("127.0.0.1", port))
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn: socket.socket) -> None:
        transport = paramiko.Transport(conn)
        transport.add_server_key(self.host_key)
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, _Folder)
        try:
            transport.start_server(server=_Server(self.authorized))
            while transport.is_active():
                transport.join(1)
        except (paramiko.SSHException, EOFError, OSError):
            pass
        finally:
            transport.close()

    def close(self) -> None:
        self.listener.close()
