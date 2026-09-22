from __future__ import annotations

import hashlib
import struct


def check_deterministic(data: bytes, expected_hash: str) -> bool:
    return hashlib.sha256(data).hexdigest() == expected_hash


def median_vote(results: list[bytes], fmt: str) -> bytes:
    """Coordinate-wise median over struct-unpacked floats; robust to <50% Byzantine.
    fmt like '2d' meaning little-endian 2 doubles. All results must unpack to same count."""
    if not results:
        raise ValueError("no results to vote on")
    n = struct.calcsize("<" + fmt)
    cols = [list(v) for v in zip(*(struct.unpack("<" + fmt, r[:n]) for r in results))]
    med = [sorted(c)[len(c) // 2] for c in cols]
    return struct.pack("<" + fmt, *med)


def majority_vote(results: list[bytes]) -> bytes:
    from collections import Counter

    if not results:
        raise ValueError("no results to vote on")
    return Counter(results).most_common(1)[0][0]


def verify_results(
    results: list[bytes], *, fmt: str | None = None, min_results: int = 1
) -> bytes:
    if len(results) < min_results:
        raise ValueError(f"got {len(results)} results, need at least {min_results}")
    if fmt:
        return median_vote(results, fmt)
    return majority_vote(results)
