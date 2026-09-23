import struct

import pytest

from bitcompute import executor
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


def test_custom_executor_requires_explicit_trust_opt_in():
    from bitcompute.executor import register

    @register
    class LocalPlugin:
        name = "test_local_plugin"

        def run(self, *, unit_uid, shard, params):
            return b"ok"

    with pytest.raises(PermissionError, match="custom executor"):
        get("test_local_plugin")
    assert get("test_local_plugin", allow_custom=True).run(
        unit_uid="u", shard=b"", params={}
    ) == b"ok"


def test_installed_plugin_entry_point_is_resolved_without_loading(monkeypatch):
    class EntryPoint:
        name = "installed_plugin"
        module = "plugin_package.executor"
        attr = "Plugin"

    def entry_points(*, group):
        assert group == "bitcompute.executors"
        return [EntryPoint()]

    monkeypatch.setattr(executor.importlib.metadata, "entry_points", entry_points)
    assert executor.custom_executor_target("installed_plugin") == (
        "plugin_package.executor", "Plugin"
    )


def test_plugin_cannot_replace_bundled_executor():
    with pytest.raises(ValueError, match="cannot replace bundled"):
        executor.register(type("Hijack", (), {"name": "mock"}))


def test_toy_training_converges():
    import bitcompute.executors.train_numpy  # noqa: F401  registers "train_numpy"

    rows = b"".join(b"%d,%d\n" % (x, 2 * x + 1) for x in range(20))
    ex = get("train_numpy")
    out = ex.run(unit_uid="u1", shard=rows, params={"lr": 0.01, "steps": 500})
    w, b = struct.unpack("<2d", out)
    assert abs(w - 2) < 0.2
    assert abs(b - 1) < 0.2
