"""System call filter of the sandboxed scan workers (libseccomp through ctypes).

Applied by a worker once its engines are loaded, before it gets any file: the
system calls a scan never needs are refused, so that code running in a
compromised worker cannot start programs, debug or reach other processes,
create namespaces, use the network, load kernel modules or use the kernel
interfaces that are the usual targets of privilege escalations. The worker is
already confined (bubblewrap) and without privileges: this filter reduces the
kernel attack surface.

YARA-X compiles its rules to native code: the filter cannot forbid memory
that is both writable and executable (MemoryDenyWriteExecute).
"""

from __future__ import annotations

import ctypes
import errno
import socket

# Values of libseccomp (seccomp.h)
_ACT_ALLOW = 0x7FFF0000
_CMP_NE = 1
_CMP_MASKED_EQ = 7
_NR_ERROR = -1


def _act_errno(code: int) -> int:
    return 0x00050000 | (code & 0x0000FFFF)


class _ArgCmp(ctypes.Structure):
    _fields_ = (
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_int),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    )


# Refused with EPERM
DENIED = (
    # programs and other processes
    "execve",
    "execveat",
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "pidfd_getfd",
    "kcmp",
    # mounts and namespaces
    "mount",
    "umount2",
    "pivot_root",
    "chroot",
    "unshare",
    "setns",
    "open_tree",
    "move_mount",
    "fsopen",
    "fsmount",
    "fsconfig",
    "fspick",
    "mount_setattr",
    # kernel and system
    "bpf",
    "perf_event_open",
    "kexec_load",
    "kexec_file_load",
    "init_module",
    "finit_module",
    "delete_module",
    "reboot",
    "swapon",
    "swapoff",
    "acct",
    "quotactl",
    "syslog",
    "keyctl",
    "add_key",
    "request_key",
    "userfaultfd",
    "open_by_handle_at",
    "name_to_handle_at",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "personality",
)
# Namespaces cannot be created by clone() either
_CLONE_NEW = (
    0x00020000,  # CLONE_NEWNS
    0x02000000,  # CLONE_NEWCGROUP
    0x04000000,  # CLONE_NEWUTS
    0x08000000,  # CLONE_NEWIPC
    0x10000000,  # CLONE_NEWUSER
    0x20000000,  # CLONE_NEWPID
    0x40000000,  # CLONE_NEWNET
)


class SeccompError(Exception):
    pass


def apply_filter() -> None:
    """Install the filter in this process (and its future threads)."""
    try:
        lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    except OSError as ex:
        raise SeccompError(f"libseccomp not available: {ex}") from ex
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_init.argtypes = (ctypes.c_uint32,)
    lib.seccomp_rule_add_array.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(_ArgCmp),
    )
    lib.seccomp_syscall_resolve_name.argtypes = (ctypes.c_char_p,)
    lib.seccomp_load.argtypes = (ctypes.c_void_p,)
    lib.seccomp_release.argtypes = (ctypes.c_void_p,)

    ctx = lib.seccomp_init(_ACT_ALLOW)
    if not ctx:
        raise SeccompError("seccomp_init failed")

    def rule(action: int, name: str, *comparisons: _ArgCmp) -> None:
        number = lib.seccomp_syscall_resolve_name(name.encode())
        if number == _NR_ERROR:
            return  # not a system call of this architecture
        array = (_ArgCmp * len(comparisons))(*comparisons)
        if lib.seccomp_rule_add_array(ctx, action, number, len(comparisons), array) < 0:
            raise SeccompError(f"cannot add the rule for {name}")

    try:
        for name in DENIED:
            rule(_act_errno(errno.EPERM), name)
        # Only Unix sockets (clamd)
        rule(
            _act_errno(errno.EAFNOSUPPORT),
            "socket",
            _ArgCmp(0, _CMP_NE, socket.AF_UNIX, 0),
        )
        for flag in _CLONE_NEW:
            rule(_act_errno(errno.EPERM), "clone", _ArgCmp(0, _CMP_MASKED_EQ, flag, flag))
        # clone3 passes its flags in memory, which seccomp cannot inspect: with
        # ENOSYS, the C library falls back to clone() to create threads
        rule(_act_errno(errno.ENOSYS), "clone3")
        # Loading also sets no_new_privs
        if lib.seccomp_load(ctx) < 0:
            raise SeccompError("seccomp_load failed")
    finally:
        lib.seccomp_release(ctx)
