"""Peer capability registry (what a node holds / can compute)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Capability:
    job_id: str
    have: set[str] = field(default_factory=set)          # piece uids held
    executors: list[str] = field(default_factory=list)   # executor names offered
    slots: int = 1                                        # concurrent units

    def wants(self, unit_uid: str) -> bool:
        return unit_uid not in self.have
