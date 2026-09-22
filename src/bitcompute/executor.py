from __future__ import annotations

import importlib
from typing import Protocol, runtime_checkable


@runtime_checkable
class Executor(Protocol):
    name: str
    def run(self, *, unit_uid: str, shard: bytes | None, params: dict) -> bytes: ...


class MockExecutor:
    name = "mock"

    def run(self, *, unit_uid, shard, params) -> bytes:
        import hashlib

        return hashlib.sha256(unit_uid.encode() + (shard or b"")).digest()


_registry: dict[str, type] = {"mock": MockExecutor}


def register(cls):
    _registry[cls.name] = cls
    return cls


def registry() -> dict[str, type]:
    return dict(_registry)


def get(name: str) -> Executor:
    if name not in _registry:
        # lazy: importing bitcompute.executors.<name> registers it
        try:
            importlib.import_module(f"bitcompute.executors.{name}")
        except ModuleNotFoundError:
            pass
    if name not in _registry:
        raise KeyError(f"unknown executor {name}")
    return _registry[name]()
