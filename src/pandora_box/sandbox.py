"""Confinement of the scan workers (scan.sandbox).

A scan worker parses hostile files with several engines: it is assumed
compromisable. Each worker is started by the kiosk process (root) as:

    bwrap [namespaces, minimal file system] setpriv [dedicated user] python

- bubblewrap gives it new PID, IPC, UTS, cgroup and network namespaces (no
  network interface at all) and a file system holding only /usr, a few files
  of /etc, the signature files of the engines (read-only), its writable cache
  and the clamd socket folder: no device, no mount point, no kiosk data;
- setpriv then switches to the dedicated user, without supplementary groups,
  capabilities or the possibility to gain privileges (no_new_privs);
- the worker installs its system call filter (pandora_box.seccomp) once its
  engines are loaded.

The worker never opens a file of the device: the kiosk opens each file and
passes the descriptor (pandora_box.workers). bubblewrap runs as root, so
unprivileged user namespaces stay disabled on the kiosk.
"""

from __future__ import annotations

import os
import pwd
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

BWRAP = "bwrap"
SETPRIV = "setpriv"
# Files of /etc needed by Python and libmagic
ETC_FILES = ("/etc/ld.so.cache", "/etc/magic", "/etc/magic.mime", "/etc/localtime")


class SandboxError(Exception):
    pass


@dataclass(frozen=True)
class Sandbox:
    user: str
    read_only: tuple[Path, ...] = ()
    writable: tuple[Path, ...] = ()

    def ids(self) -> tuple[int, int]:
        """User and group ids of the sandbox user."""
        try:
            entry = pwd.getpwnam(self.user)
        except KeyError:
            raise SandboxError(f"scan.sandbox_user: no user {self.user!r}") from None
        if entry.pw_uid == 0:
            raise SandboxError("scan.sandbox_user must not be root")
        return entry.pw_uid, entry.pw_gid

    def check(self) -> None:
        """Raise SandboxError when workers cannot be sandboxed here."""
        hint = " (set scan.sandbox = false for development)"
        if os.geteuid() != 0:
            raise SandboxError("the scan sandbox needs root" + hint)
        for program in (BWRAP, SETPRIV):
            if shutil.which(program) is None:
                raise SandboxError(f"the scan sandbox needs {program}" + hint)
        self.ids()

    def prepare(self) -> None:
        """Create the writable folders, owned by the sandbox user."""
        uid, gid = self.ids()
        for path in self.writable:
            path.mkdir(parents=True, exist_ok=True)
            os.chown(path, uid, gid)
            path.chmod(0o700)

    def wrap(self, command: Sequence[str]) -> list[str]:
        """The command line starting command in the sandbox."""
        uid, gid = self.ids()
        argv = [
            BWRAP,
            "--die-with-parent",
            "--new-session",
            "--unshare-ipc",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-uts",
            "--unshare-cgroup-try",
            # Only to let setpriv switch to the sandbox user, then drop them all
            "--cap-add",
            "CAP_SETUID",
            "--cap-add",
            "CAP_SETGID",
            "--cap-add",
            "CAP_SETPCAP",
            "--ro-bind",
            "/usr",
            "/usr",
        ]
        # Merged /usr: /lib, /bin... are links to /usr
        for name in ("bin", "sbin", "lib", "lib64"):
            link = Path("/", name)
            if link.is_symlink():
                argv += ["--symlink", str(link.readlink()), str(link)]
        for etc_file in ETC_FILES:
            argv += ["--ro-bind-try", etc_file, etc_file]
        # A new, empty /tmp of the sandbox
        argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]  # noqa: S108
        # Python environment of the kiosk, when it is not under /usr
        python = {Path(sys.prefix), Path(__file__).resolve().parents[1]}
        read_only = [p for p in sorted(python) if not p.is_relative_to("/usr")]
        read_only += self.read_only
        # bubblewrap creates the missing parents of a mount point for root
        # only (0700): the sandbox user must be able to go through them
        created: set[Path] = set()
        for path in [*read_only, *self.writable]:
            for parent in reversed(path.parents):
                if parent == Path("/") or parent in created or parent.is_relative_to("/usr"):
                    continue
                created.add(parent)
                argv += ["--perms", "0755", "--dir", str(parent)]
        for path in read_only:
            argv += ["--ro-bind-try", str(path), str(path)]
        for path in self.writable:
            argv += ["--bind", str(path), str(path)]
        argv += ["--chdir", "/", "--"]
        argv += [
            SETPRIV,
            f"--reuid={uid}",
            f"--regid={gid}",
            "--clear-groups",
            "--inh-caps=-all",
            "--bounding-set=-all",
            "--no-new-privs",
            "--",
            *command,
        ]
        return argv
