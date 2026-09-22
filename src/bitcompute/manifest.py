from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True, slots=True)
class WorkUnit:
    uid: str
    round: int
    shard: str | None = None   # None => inference unit (input ref)
    input_ref: str | None = None


@dataclass(frozen=True)
class JobManifest:
    name: str
    mode: str                    # "train" | "infer"
    shards: list[bytes]
    units: list[WorkUnit]
    executor: str
    params: dict
    redundancy: int = 1
    job_id: str = field(default="", compare=False)

    @classmethod
    def create(cls, **kw) -> "JobManifest":
        m = cls(**kw)
        if m.mode not in ("train", "infer"):
            raise ValueError(f"mode must be 'train' or 'infer', got {m.mode!r}")
        if m.redundancy < 1:
            raise ValueError(f"redundancy must be >= 1, got {m.redundancy}")
        if m.mode == "train":
            for u in m.units:
                if u.shard is None:
                    raise ValueError(f"train unit {u.uid!r} requires shard")
        else:  # infer
            for u in m.units:
                if u.input_ref is None:
                    raise ValueError(f"infer unit {u.uid!r} requires input_ref")
        object.__setattr__(m, "job_id", m._hash())
        return m

    def _hash(self) -> str:
        payload = {
            "name": self.name, "mode": self.mode,
            "shards": [hashlib.sha256(s).hexdigest() for s in self.shards],
            "units": [asdict(u) for u in self.units],
            "executor": self.executor, "params": self.params,
            "redundancy": self.redundancy,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def rounds(self) -> list[int]:
        return sorted({u.round for u in self.units})

    def to_torrent_payload(self) -> bytes:
        return json.dumps({
            "job_id": self.job_id, "name": self.name, "mode": self.mode,
            "units": [asdict(u) for u in self.units],
            "executor": self.executor, "params": self.params,
            "redundancy": self.redundancy,
            "shards": {hashlib.sha256(s).hexdigest(): s.hex() for s in self.shards},
        }).encode()
