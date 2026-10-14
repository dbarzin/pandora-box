"""Test corpus of the virtual machine tests.

The files of the emulated USB key, the verdicts expected for each of them, and
the test-only signatures that detect them (installed by the image test
profile). Also a command line tool:

    python3 corpus.py key FOLDER         write the key files into FOLDER
    python3 corpus.py signatures FOLDER  write the test signature set into FOLDER
    python3 corpus.py canary FILE        write the dm-verity canary file
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

# EICAR is stored encoded in the repository (tests/samples.py)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from samples import eicar  # type: ignore[import-not-found]

YARA_MARKER = b"PANDORA-BOX-VM-YARA-MARKER"
YARA_RULE = f"""
rule PandoraBox_VM_Marker {{
    meta: score = 90
    strings: $a = "{YARA_MARKER.decode()}"
    condition: $a
}}
"""
MALWAREBAZAAR_SAMPLE = b"pretend malware sample listed in MalwareBazaar (VM test)\n"
KNOWN_FILE = b"a well known file listed in hashlookup (VM test)\n"
# Detected by MalwareBazaar only in the signature set of the update test
NEW_SAMPLE = b"pretend malware sample listed by a signature update (VM test)\n"
# File of the root filesystem of the test image, modified on the disk by the
# dm-verity test. Its first line must be unique in the image; it fills whole
# filesystem blocks, so that the modified block holds nothing else.
VERITY_CANARY_PATH = "/usr/share/pandora-box/verity-canary"
VERITY_CANARY_MARKER = b"PANDORA-BOX-VERITY-CANARY-" + b"5d1c0a7e93f2b864" * 4 + b"\n"
VERITY_CANARY = (VERITY_CANARY_MARKER * (3 * 4096 // len(VERITY_CANARY_MARKER) + 1))[: 3 * 4096]

# Files of the USB key and the engine expected to detect each of them
# ("" when the file must be reported clean and stay on the key)
KEY: dict[str, tuple[bytes, str]] = {
    "readme.txt": (b"hello from the PandoraBox VM test\n", ""),
    "known.txt": (KNOWN_FILE, ""),
    "docs/eicar.com": (eicar(), "clamav"),
    "docs/marker.bin": (b"xx " + YARA_MARKER + b" xx\n", "yara"),
    "sample.bin": (MALWAREBAZAAR_SAMPLE, "malwarebazaar"),
}


# PDF with JavaScript run at opening: suspicious (engines.heuristics)
ACTIVE_PDF = (
    b"%PDF-1.7\n1 0 obj << /Type /Catalog /OpenAction 2 0 R >> endobj\n"
    b"2 0 obj << /S /JavaScript /JS (app.alert('PandoraBox VM test')) >> endobj\n%%EOF\n"
)


def encrypted_zip(name: str = "secret.txt", content: bytes = b"secret document\n") -> bytes:
    """A zip archive encrypted with a password (ZipCrypto): it cannot be scanned.

    The standard library only reads such archives: written by hand.
    """
    import struct
    import zlib

    keys = [0x12345678, 0x23456789, 0x34567890]

    def crc(value: int, byte: int) -> int:
        return zlib.crc32(bytes([byte]), value ^ 0xFFFFFFFF) ^ 0xFFFFFFFF

    def update(byte: int) -> None:
        keys[0] = crc(keys[0], byte)
        keys[1] = ((keys[1] + (keys[0] & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        keys[2] = crc(keys[2], keys[1] >> 24)

    def encrypt(data: bytes) -> bytes:
        out = bytearray()
        for byte in data:
            t = (keys[2] | 2) & 0xFFFF
            out.append(byte ^ (((t * (t ^ 1)) >> 8) & 0xFF))
            update(byte)
        return bytes(out)

    for byte in b"password":
        update(byte)
    checksum = zlib.crc32(content) & 0xFFFFFFFF
    body = encrypt(bytes(11) + bytes([checksum >> 24])) + encrypt(content)
    filename = name.encode()
    sizes = (checksum, len(body), len(content), len(filename))
    local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 1, 0, 0, 0, *sizes, 0) + filename + body
    central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 1, 0, 0, 0, *sizes,
                          0, 0, 0, 0, 0, 0) + filename  # fmt: skip
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), len(local), 0)
    return local + central + end


def write_key(folder: Path) -> None:
    for path, (content, _) in KEY.items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


# Files of the test signature set (the default paths of the engines, but YARA)
MALWAREBAZAAR_DB = "malwarebazaar/malwarebazaar.sha256.bin"
HASHLOOKUP_BLOOM = "hashlookup/hashlookup-full.bloom"
YARA_RULES = "yara/vm-test/rules.yar"
CLAMAV_DB = "clamav/pandora-box-test.hdb"


def write_signatures(folder: Path, malwarebazaar: tuple[bytes, ...] = ()) -> None:
    """Write a signature set detecting the key files, and nothing else.

    malwarebazaar: more samples to detect (signature update test).
    """
    from pandora_box.bloom import write_filter
    from pandora_box.hashdb import write_database

    for name in (MALWAREBAZAAR_DB, HASHLOOKUP_BLOOM, YARA_RULES, CLAMAV_DB):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
    samples = (MALWAREBAZAAR_SAMPLE, *malwarebazaar)
    write_database([hashlib.sha256(s).digest() for s in samples], folder / MALWAREBAZAAR_DB)
    write_filter(folder / HASHLOOKUP_BLOOM, [hashlib.sha1(KNOWN_FILE).hexdigest().upper().encode()])
    (folder / YARA_RULES).write_text(YARA_RULE)
    sample = eicar()
    (folder / CLAMAV_DB).write_text(
        f"{hashlib.md5(sample).hexdigest()}:{len(sample)}:PandoraBox.Test.EICAR\n"
    )


def write_canary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(VERITY_CANARY)


def main(argv: list[str]) -> int:
    commands = {"key": write_key, "signatures": write_signatures, "canary": write_canary}
    if len(argv) != 2 or argv[0] not in commands:
        print(__doc__, file=sys.stderr)
        return 2
    commands[argv[0]](Path(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
