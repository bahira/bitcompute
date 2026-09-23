"""Command-line interface for bitcompute."""
from __future__ import annotations

import argparse
import json
import sys

from bitcompute import __version__, node


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bitcompute",
        description="Distribute deterministic compute jobs through a BitTorrent swarm.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    seed = sub.add_parser("seed", help="publish a job and aggregate worker results")
    seed.add_argument("job_dir")
    seed.add_argument("--port", type=int, default=6881)
    seed.add_argument("--workers", type=int, nargs="+", default=None,
                      help="worker ports, e.g. --workers 6882 6883")
    seed.add_argument("--announce-host", default="0.0.0.0",
                      help="control-plane bind address")
    seed.add_argument("--announce-port", type=int,
                      help="result control port (default: seed port + 1000)")
    seed.add_argument("--collect-timeout", type=float, default=75.0, metavar="SECONDS")
    seed.add_argument("--verify-timeout", type=float, default=25.0, metavar="SECONDS")

    worker = sub.add_parser("worker", help="join a job swarm and publish computed results")
    worker.add_argument("--magnet", required=True, help="40-character BitTorrent v1 info hash")
    worker.add_argument("--job-dir", required=True)
    worker.add_argument("--port", type=int, default=6882)
    worker.add_argument("--seed-host", default="127.0.0.1")
    worker.add_argument("--seed-port", type=int, default=6881)
    worker.add_argument("--announce-port", type=int,
                        help="seed control port (default: seed port + 1000)")
    worker.add_argument("--announce-timeout", type=float, default=3.0, metavar="SECONDS")
    worker.add_argument("--fetch-timeout", type=float, default=120.0, metavar="SECONDS")
    worker.add_argument("--result-grace", type=float, default=20.0, metavar="SECONDS",
                        help="seconds to seed the result before exiting")

    status = sub.add_parser("status", help="print the latest job summary")
    status.add_argument("job_dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "seed":
            ports = tuple(args.workers) if args.workers else (6882, 6883)
            def ready(event: dict) -> None:
                print(
                    "bitcompute: ready " + json.dumps(event, ensure_ascii=False),
                    file=sys.stderr, flush=True,
                )

            info = node.seed_job(
                args.job_dir, port=args.port, worker_ports=ports,
                announce_host=args.announce_host, announce_port=args.announce_port,
                collect_timeout=args.collect_timeout, verify_timeout=args.verify_timeout,
                on_ready=ready,
            )
            print(json.dumps(info, ensure_ascii=False))
        elif args.cmd == "worker":
            body = node.run_worker(
                args.magnet, args.job_dir, args.port, args.seed_port,
                seed_host=args.seed_host, announce_port=args.announce_port,
                announce_timeout=args.announce_timeout,
                fetch_timeout=args.fetch_timeout, result_grace=args.result_grace,
            )
            print(json.dumps({"worker_port": args.port, "done": sorted(body["units"])}))
        else:
            print(json.dumps(node.status(args.job_dir), ensure_ascii=False))
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
        print(f"bitcompute: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
