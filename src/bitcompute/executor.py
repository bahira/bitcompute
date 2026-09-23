from __future__ import annotations

import importlib
import importlib.metadata
import re
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
_BUILTIN_MODULES = {
    "train_numpy": "bitcompute.executors.train_numpy",
    "infer_llama": "bitcompute.executors.infer_llama",
}
_EXECUTOR_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def register(cls):
    name = getattr(cls, "name", None)
    if not isinstance(name, str) or not _EXECUTOR_NAME.fullmatch(name):
        raise ValueError("executor names must match [a-z][a-z0-9_]{0,63}")
    builtin_module = {"mock": "bitcompute.executor", **_BUILTIN_MODULES}.get(name)
    if builtin_module is not None and cls.__module__ != builtin_module:
        raise ValueError(f"cannot replace bundled executor {name!r}")
    _registry[name] = cls
    return cls


def registry() -> dict[str, type]:
    return dict(_registry)


def is_builtin(name: str) -> bool:
    return name == "mock" or name in _BUILTIN_MODULES


def custom_executor_target(name: str) -> tuple[str, str] | None:
    """Return an import target without importing a third-party plugin in-process."""
    cls = _registry.get(name)
    if cls is not None and not is_builtin(name):
        return cls.__module__, cls.__qualname__

    matches = [
        entry for entry in importlib.metadata.entry_points(group="bitcompute.executors")
        if entry.name == name
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError(f"multiple installed plugins use executor name {name!r}")
    entry = matches[0]
    module_name, qualname = entry.module, entry.attr
    identifier = r"[A-Za-z_][A-Za-z0-9_]*"
    if (
        not module_name
        or "__" in module_name
        or any(not re.fullmatch(identifier, part) for part in module_name.split("."))
        or not qualname
        or "__" in qualname
        or any(not re.fullmatch(identifier, part) for part in qualname.split("."))
    ):
        raise ValueError(f"invalid entry point target for executor {name!r}")
    return module_name, qualname


def get(name: str, *, allow_custom: bool = False) -> Executor:
    """Load a bundled executor; custom installed plugins require explicit opt-in.

    Manifest contents never choose an arbitrary Python import path. An unknown
    executor is rejected unless the operator has explicitly enabled a plugin
    that was already registered by trusted local code.
    """
    if not isinstance(name, str) or not _EXECUTOR_NAME.fullmatch(name):
        raise KeyError("invalid executor name")
    if name not in _registry and name in _BUILTIN_MODULES:
        importlib.import_module(_BUILTIN_MODULES[name])
    if name not in _registry:
        raise KeyError(f"unknown executor {name}")
    if name not in {"mock", *_BUILTIN_MODULES} and not allow_custom:
        raise PermissionError(
            f"custom executor {name!r} is disabled; only enable plugins installed from trusted code"
        )
    return _registry[name]()
