"""Fetch-latency bench: seed N-byte torrents, time bt.wait on the fetch side."""
from __future__ import annotations

import tempfile
import time

from bitcompute import torrent as bt

SIZES = (16 * 1024, 256 * 1024, 2 * 1024 * 1024)
BASE = 7461


def main() -> None:
    dest = tempfile.mkdtemp()
    rows: list[tuple[int, int, bool, float]] = []
    for i, n in enumerate(SIZES):
        seed_port = BASE + i * 3
        _, sess, hex_ = bt.seed_bytes(bytes(n), "b.bin", seed_port)
        t0 = time.time()
        h, fsess = bt.fetch(hex_, dest, seed_port + 1, seed_port=seed_port,
                            name="b.bin")
        ok = bt.wait(h, timeout=30)
        dt = time.time() - t0
        fsess.pause()
        sess.pause()
        rows.append((n, seed_port, ok, dt))
    print(f"{'bytes':>10} {'seed_port':>10} {'ok':>6} {'latency_s':>10}")
    for n, sp, ok, dt in rows:
        print(f"{n:>10} {sp:>10} {str(ok):>6} {dt:>10.3f}")


if __name__ == "__main__":
    main()
