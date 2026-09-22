"""Tit-for-tat unchoke accounting (BitTorrent-style, no tokens)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ledger:
    peers: dict[str, list[int]] = field(default_factory=dict)  # peer -> [contributed, received]

    def record(self, peer: str, contributed: int, received: int) -> None:
        c, r = self.peers.get(peer, [0, 0])
        self.peers[peer] = [c + contributed, r + received]

    def net(self, peer: str) -> int:
        c, r = self.peers.get(peer, [0, 0])
        return c - r


def should_unchoke(led: Ledger, max_unchoked: int) -> list[str]:
    """Rank by net contribution; leave 1 slot for optimistic unchoke."""
    if max_unchoked <= 0:
        return []
    ranked = sorted(led.peers, key=lambda p: led.net(p), reverse=True)
    n_est = max(0, max_unchoked - 1)
    out = [p for p in ranked[:n_est] if led.net(p) >= 0]
    opt = next((p for p in ranked if p not in out), None)
    if opt is not None:
        out.append(opt)
    return out[:max_unchoked]
