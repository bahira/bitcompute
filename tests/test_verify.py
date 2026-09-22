import hashlib
import struct

import pytest

from bitcompute.verify import (
    check_deterministic,
    majority_vote,
    median_vote,
    verify_results,
)


def test_hash_gate_rejects_tamper():
    good = b"weights-round-7"
    expected = hashlib.sha256(good).hexdigest()
    assert check_deterministic(good, expected)
    assert not check_deterministic(b"weights-round-7\nevil", expected)


def test_median_vote_blinds_one_byzantine():
    honest = [
        struct.pack("<2d", 1.9, 1.05),
        struct.pack("<2d", 2.1, 0.95),
        struct.pack("<2d", 2.0, 1.0),
    ]
    liar = struct.pack("<2d", 99.0, 99.0)
    w, b = struct.unpack("<2d", median_vote([liar, *honest], "2d"))
    assert abs(w - 2) < 0.3
    assert abs(b - 1) < 0.3


def test_majority_vote_picks_mode():
    a, b = b"result-a", b"result-b"
    assert majority_vote([a, a, b, b, a]) == a
    assert majority_vote([b, b, a]) == b


def test_empty_results_raise():
    with pytest.raises(ValueError):
        median_vote([], "2d")
    with pytest.raises(ValueError):
        majority_vote([])


def test_verify_results_min_threshold():
    two = [b"x", b"y"]
    with pytest.raises(ValueError):
        verify_results(two, min_results=3)
    assert verify_results(two, min_results=2) == b"x"
    packed = struct.pack("<d", 4.5)
    assert verify_results([packed], fmt="d") == packed
