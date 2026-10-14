"""Read-only reader for Bloom filters in the DCSO bloom format (version 1).

This is the format of the CIRCL hashlookup filter, also read and written by
the DCSO "bloom" Go tool and the "flor" Python library. The file is mapped in
memory instead of being loaded (the CIRCL filter is about 1 GB), so the
scan workers share it through the page cache.

Layout (little-endian): flags (uint64, version 1 in the low byte), capacity n
(uint64), false positive probability p (float64), number of hash functions k
(uint64), number of bits m (uint64), number of elements N (uint64), then the
bit array in 64-bit blocks, then optional data.

Fingerprint of a value: FNV-1 (64 bit) of its bytes, reduced modulo a prime,
then k rounds of multiplication by a second prime; each round sets bit
(h mod m). Bit i is bit (i mod 8) of byte (i div 8) of the bit array.

A Bloom filter has false positives (probability p) and no false negatives.
"""

from __future__ import annotations

import math
import mmap
import os
import struct
from pathlib import Path

_HEADER = struct.Struct("<QQdQQQ")
HEADER_SIZE = _HEADER.size  # 48
_MOD = 18446744073709551557  # largest prime below 2**64
_MUL = 18446744073709550147
_MASK = 0xFFFFFFFFFFFFFFFF
_FNV_OFFSET = 14695981039346656037
_FNV_PRIME = 1099511628211


class BloomError(Exception):
    pass


def fnv1(value: bytes) -> int:
    h = _FNV_OFFSET
    for byte in value:
        h = (h * _FNV_PRIME) & _MASK
        h ^= byte
    return h


class BloomFilter:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            with path.open("rb") as f:
                size = os.fstat(f.fileno()).st_size
                if size < HEADER_SIZE:
                    raise BloomError(f"{path}: truncated header")
                self._mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except OSError as ex:
            raise BloomError(f"{path}: {ex.strerror}") from ex
        flags, n, p, k, m, count = _HEADER.unpack_from(self._mm, 0)
        if flags & 0xFF != 1:
            raise BloomError(f"{path}: unsupported DCSO bloom version {flags & 0xFF}")
        if m == 0 or not 1 <= k <= 64 or not 0 < p < 1:
            raise BloomError(f"{path}: invalid parameters (m={m}, k={k}, p={p})")
        self._bytes = math.ceil(m / 64) * 8
        if size < HEADER_SIZE + self._bytes:
            raise BloomError(f"{path}: truncated bit array")
        self.capacity: int = n
        self.fp_rate: float = p
        self.hashes: int = k
        self.bits: int = m
        self.count: int = count

    def __contains__(self, value: object) -> bool:
        if not isinstance(value, bytes):
            return False
        mm = self._mm
        h = fnv1(value) % _MOD
        for _ in range(self.hashes):
            h = ((h * _MUL) & _MASK) % _MOD
            bit = h % self.bits
            if not mm[HEADER_SIZE + bit // 8] & (1 << (bit % 8)):
                return False
        return True

    def close(self) -> None:
        self._mm.close()


def write_filter(path: Path, values: list[bytes], fp_rate: float = 0.001) -> None:
    """Write a filter in the DCSO format (tests and development only)."""
    n = max(1, len(values))
    m = math.ceil(-n * math.log(fp_rate) / math.log(2) ** 2)
    k = math.ceil(math.log(2) * m / n)
    bits = bytearray(math.ceil(m / 64) * 8)
    for value in values:
        h = fnv1(value) % _MOD
        for _ in range(k):
            h = ((h * _MUL) & _MASK) % _MOD
            bit = h % m
            bits[bit // 8] |= 1 << (bit % 8)
    with path.open("wb") as f:
        f.write(_HEADER.pack(1, n, fp_rate, k, m, len(values)))
        f.write(bits)
