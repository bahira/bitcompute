"""CLI: bitcompute seed|worker|status."""
from __future__ import annotations

import argparse
import json
import sys

from bitcompute import node


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bitcompute")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed")
    s.add_argument("job_dir")
    s.add_argument("--port", type=int, default=6881)
    s.add_argument("--workers", type=int, nargs="+", default=None,
                   help="worker ports, e.g. --workers 6882 6883")

    w = sub.add_parser("worker")
    w.add_argument("--magnet", required=True)
    w.add_argument("--job-dir", required=True)
    w.add_argument("--port", type=int, default=6882)
    w.add_argument("--seed-port", type=int, default=6881)

    st = sub.add_parser("status")
    st.add_argument("job_dir")

    a = p.parse_args(argv)
    if a.cmd == "seed":
        ports = tuple(a.workers) if a.workers else (6882, 6883)
        info = node.seed_job(a.job_dir, port=a.port, worker_ports=ports)
        print(json.dumps(info))
    elif a.cmd == "worker":
        body = node.run_worker(a.magnet, a.job_dir, a.port, a.seed_port)
        print(json.dumps({"worker_port": a.port, "done": sorted(body["units"])}))
    elif a.cmd == "status":
        print(json.dumps(node.status(a.job_dir)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
