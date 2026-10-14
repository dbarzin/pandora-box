"""Text helpers for untrusted strings (file names, device labels)."""

from __future__ import annotations

import base64
import os
import unicodedata


def human_size(size: float, decimal_places: int = 1) -> str:
    """Convert a size in bytes to a human readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0:
            return f"{size:.{decimal_places}f}{unit}"
        size /= 1024.0
    return f"{size:.{decimal_places}f}PB"


def printable(value: object) -> str:
    """Make an untrusted string safe to display on a terminal.

    Control characters (including terminal escape sequences) are replaced and
    undecodable bytes in file names are shown as escapes.
    """
    text = "" if value is None else str(value)
    text = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return "".join("?" if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in text)


def escape(value: str) -> str:
    """Escape an untrusted file name for logs and reports.

    Undecodable bytes become \\xNN, control and invisible format characters
    become \\xNN or \\uNNNN, and backslashes are doubled, so that the result
    is printable, valid UTF-8 and unambiguous.
    """
    out: list[str] = []
    for c in value:
        code = ord(c)
        if 0xDC80 <= code <= 0xDCFF:  # undecodable byte (surrogate escape)
            out.append(f"\\x{code - 0xDC00:02x}")
        elif c == "\\":
            out.append("\\\\")
        elif unicodedata.category(c) in ("Cc", "Cf", "Cs", "Zl", "Zp"):
            if code < 0x100:
                out.append(f"\\x{code:02x}")
            elif code < 0x10000:
                out.append(f"\\u{code:04x}")
            else:
                out.append(f"\\U{code:08x}")
        else:
            out.append(c)
    return "".join(out)


def path_b64(value: str) -> str:
    """Exact bytes of a file name, base64 encoded (for audit)."""
    return base64.b64encode(os.fsencode(value)).decode("ascii")
