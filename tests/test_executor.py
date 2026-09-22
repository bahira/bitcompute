import struct

import pytest

from bitcompute.executor import MockExecutor, get, registry


def test_mock_deterministic_and_registered():
    r = registry()
    assert "mock" in r
    assert r["mock"] is MockExecutor
    ex = get("mock")
    assert isinstance(ex, MockExecutor)
    a = ex.run(unit_uid="u1", shard=b"s", params={})
    b = ex.run(unit_uid="u1", shard=b"s", params={})
    assert a == b
    assert len(a) == 32
    assert a != ex.run(unit_uid="u2", shard=b"s", params={})


def test_registry_get_unknown_raises():
    with pytest.raises(KeyError):
        get("does-not-exist")


def test_toy_training_converges():
    import bitcompute.executors.train_numpy  # noqa: F401  registers "train_numpy"

    rows = b"".join(b"%d,%d\n" % (x, 2 * x + 1) for x in range(20))
    ex = get("train_numpy")
    out = ex.run(unit_uid="u1", shard=rows, params={"lr": 0.01, "steps": 500})
    w, b = struct.unpack("<2d", out)
    assert abs(w - 2) < 0.2
    assert abs(b - 1) < 0.2
