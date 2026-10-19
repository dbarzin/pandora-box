"""Make the microcode initrd of mkosi reproducible, in the builder image.

mkosi 25.3 builds the microcode initrd (the .ucode section of the unified
kernel image) from directories created at build time, with their mtime in
the cpio archive, and concatenates the microcode files in the order of the
filesystem. Their mtime is set to SOURCE_DATE_EPOCH and the files sorted.
The build of the builder image fails when mkosi no longer has this code.
"""

from __future__ import annotations

import sys
from pathlib import Path

import mkosi

SOURCE = Path(mkosi.__file__)
PATCHES = [
    (
        "                for p in amd.iterdir():\n",
        "                for p in sorted(amd.iterdir()):\n",
    ),
    (
        "                for p in intel.iterdir():\n",
        "                for p in sorted(intel.iterdir()):\n",
    ),
    (
        "    make_cpio(root, microcode, sandbox=context.sandbox)\n",
        "    if context.config.source_date_epoch is not None:\n"
        "        for p in [root, *root.rglob('*')]:\n"
        "            os.utime(p, (context.config.source_date_epoch,) * 2, follow_symlinks=False)\n"
        "    make_cpio(root, microcode, sandbox=context.sandbox)\n",
    ),
]


def main() -> int:
    text = SOURCE.read_text()
    for old, new in PATCHES:
        if text.count(old) != 1:
            print(f"{SOURCE}: cannot patch {old.strip()!r}", file=sys.stderr)
            return 1
        text = text.replace(old, new)
    SOURCE.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
