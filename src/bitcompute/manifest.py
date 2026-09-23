from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 1
MAX_UNITS = 100_000
MAX_SHARDS = 10_000


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _safe_name(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError(f"{field_name} must be a non-empty relative file name")
    if "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"unsafe {field_name}: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class WorkUnit:
    uid: str
    round: int
    shard: str | None = None
    input_ref: str | None = None


@dataclass(frozen=True)
class JobManifest:
    name: str
    mode: str
    shards: list[bytes]
    units: list[WorkUnit]
    executor: str
    params: dict[str, Any]
    redundancy: int = 1
    shard_names: list[str] = field(default_factory=list)
    job_id: str = field(default="", compare=False)

    @classmethod
    def create(cls, **kw: Any) -> JobManifest:
        m = cls(**kw)
        m._validate()
        object.__setattr__(m, "job_id", m._hash())
        return m

    @classmethod
    def from_torrent_payload(cls, raw: bytes, *, max_bytes: int = 64 * 1024 * 1024) -> JobManifest:
        """Parse and authenticate a manifest received from an untrusted swarm."""
        if len(raw) > max_bytes:
            raise ValueError(f"manifest exceeds the {max_bytes}-byte limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("manifest root must be an object")
            if payload.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
                raise ValueError("unsupported manifest schema version")
            shards = [bytes.fromhex(item) for item in payload["shards"]]
            units = [WorkUnit(**item) for item in payload["units"]]
            manifest = cls.create(
                name=payload["name"], mode=payload["mode"], shards=shards,
                units=units, executor=payload["executor"], params=payload["params"],
                redundancy=payload.get("redundancy", 1),
                shard_names=payload.get("shard_names", []),
            )
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid manifest payload: {exc}") from exc
        if payload.get("job_id") != manifest.job_id:
            raise ValueError("manifest job_id does not match its content")
        return manifest

    def _validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 200:
            raise ValueError("name must contain 1 to 200 characters")
        if self.mode not in ("train", "infer"):
            raise ValueError(f"mode must be 'train' or 'infer', got {self.mode!r}")
        if not isinstance(self.executor, str) or not self.executor or len(self.executor) > 100:
            raise ValueError("executor must be a non-empty string")
        if not isinstance(self.params, Mapping):
            raise ValueError("params must be an object")
        try:
            _canonical_json(self.params)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"params must be finite JSON values: {exc}") from exc
        if not isinstance(self.redundancy, int) or isinstance(self.redundancy, bool) or self.redundancy < 1:
            raise ValueError(f"redundancy must be >= 1, got {self.redundancy!r}")
        if len(self.units) > MAX_UNITS or len(self.shards) > MAX_SHARDS:
            raise ValueError("manifest contains too many units or shards")
        # shard_names was optional in the original public API. Keep unnamed
        # in-memory manifests valid, while requiring a one-to-one mapping as
        # soon as names are supplied (local job loading always supplies them).
        if self.shard_names and len(self.shards) != len(self.shard_names):
            raise ValueError("shards and shard_names must have the same length")
        names = [_safe_name(name, "shard name") for name in self.shard_names]
        if len(names) != len(set(names)):
            raise ValueError("shard names must be unique")
        if not self.units:
            raise ValueError("manifest must contain at least one work unit")
        uids: set[str] = set()
        available = set(names)
        for unit in self.units:
            if not isinstance(unit.uid, str) or not unit.uid or len(unit.uid) > 200:
                raise ValueError("unit uid must contain 1 to 200 characters")
            if unit.uid in uids:
                raise ValueError(f"duplicate unit uid {unit.uid!r}")
            uids.add(unit.uid)
            if not isinstance(unit.round, int) or isinstance(unit.round, bool) or unit.round < 0:
                raise ValueError(f"unit {unit.uid!r} has an invalid round")
            ref = unit.shard if self.mode == "train" else unit.input_ref
            if ref is None:
                raise ValueError(f"{self.mode} unit {unit.uid!r} requires an input reference")
            # Training shards must exist. Inference input_ref may also be an
            # inline prompt, which is encoded as UTF-8 by the worker.
            if self.mode == "train":
                _safe_name(ref, "unit input reference")
                if names and ref not in available:
                    raise ValueError(f"unit {unit.uid!r} references unknown shard {ref!r}")
            elif not isinstance(ref, str) or not ref or len(ref) > 1_000_000:
                raise ValueError(f"unit {unit.uid!r} has an invalid input reference")

    def _identity(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "name": self.name,
            "mode": self.mode,
            "shards": [hashlib.sha256(s).hexdigest() for s in self.shards],
            "shard_names": self.shard_names,
            "units": [asdict(u) for u in self.units],
            "executor": self.executor,
            "params": self.params,
            "redundancy": self.redundancy,
        }

    def _hash(self) -> str:
        return hashlib.sha256(_canonical_json(self._identity())).hexdigest()

    def rounds(self) -> list[int]:
        return sorted({u.round for u in self.units})

    def to_torrent_payload(self) -> bytes:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "job_id": self.job_id,
            "name": self.name,
            "mode": self.mode,
            "units": [asdict(u) for u in self.units],
            "executor": self.executor,
            "params": self.params,
            "redundancy": self.redundancy,
            "shard_names": self.shard_names,
            "shards": [s.hex() for s in self.shards],
        }
        return _canonical_json(payload)
