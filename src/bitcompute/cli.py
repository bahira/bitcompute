"""Command-line interface for bitcompute."""
from __future__ import annotations

import argparse
import json
import sys

from bitcompute import __version__, node, security


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
    seed.add_argument("--credit-per-unit", type=int, default=1,
                      help="internal credits per verified worker-unit result")
    seed.add_argument("--settlement-ledger", help="SQLite ledger path (default: job dir)")
    seed.add_argument("--identity-key", help="coordinator Ed25519 private PEM (secure mode)")
    seed.add_argument("--encryption-key", help="shared 32-byte AES key file (secure mode)")
    seed.add_argument(
        "--worker-key", action="append", default=[], metavar="PORT=PUBLIC_KEY",
        help="pin a worker public key; repeat once for each --workers port",
    )
    seed.add_argument(
        "--insecure-legacy", action="store_true",
        help="allow unsigned, unencrypted legacy mode (trusted networks only)",
    )

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
    worker.add_argument("--identity-key", help="worker Ed25519 private PEM (secure mode)")
    worker.add_argument("--trusted-seed-key", help="pinned coordinator Ed25519 public PEM")
    worker.add_argument("--encryption-key", help="shared 32-byte AES key file (secure mode)")
    worker.add_argument(
        "--insecure-legacy", action="store_true",
        help="accept unsigned, unencrypted jobs (trusted networks only)",
    )
    worker.add_argument(
        "--allow-custom-executor", action="store_true",
        help="allow a custom executor installed locally; requires bubblewrap on Linux",
    )

    status = sub.add_parser("status", help="print the latest job summary")
    status.add_argument("job_dir")

    keys = sub.add_parser("keygen", help="generate identity and/or shared encryption keys")
    keys.add_argument("--identity", help="path for a new Ed25519 private key")
    keys.add_argument("--encryption-key", help="path for a new shared AES-256 key")
    return parser


def _print_error(exc: BaseException) -> None:
    message = f"bitcompute: error: {exc}\n"
    try:
        sys.stderr.write(message)
    except UnicodeEncodeError:
        encoding = sys.stderr.encoding or "utf-8"
        safe_message = message.encode(encoding, errors="backslashreplace").decode(encoding)
        sys.stderr.write(safe_message)


def _parse_worker_key_specs(specs: list[str]) -> dict[int, str]:
    result: dict[int, str] = {}
    for spec in specs:
        port_text, separator, path = spec.partition("=")
        if not separator or not port_text or not path:
            raise ValueError("--worker-key must use PORT=PUBLIC_KEY format")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError(f"invalid worker-key port: {port_text!r}") from exc
        if port in result:
            raise ValueError(f"duplicate --worker-key for port {port}")
        result[port] = path
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "seed":
            ports = tuple(args.workers) if args.workers else (6882, 6883)
            secure_args = (args.identity_key, args.encryption_key, args.worker_key)
            if args.insecure_legacy:
                if any(secure_args):
                    raise ValueError("do not combine --insecure-legacy with key options")
                worker_keys = None
            else:
                if not args.identity_key or not args.encryption_key or not args.worker_key:
                    raise ValueError(
                        "secure seed mode needs --identity-key, --encryption-key, and "
                        "one --worker-key PORT=PUBLIC_KEY per worker; for trusted local "
                        "tests, explicitly pass --insecure-legacy"
                    )
                worker_keys = _parse_worker_key_specs(args.worker_key)
                if set(worker_keys) != set(ports):
                    raise ValueError("provide exactly one --worker-key for every --workers port")

            def ready(event: dict) -> None:
                print(
                    "bitcompute: ready " + json.dumps(event, ensure_ascii=True),
                    file=sys.stderr, flush=True,
                )

            info = node.seed_job(
                args.job_dir, port=args.port, worker_ports=ports,
                announce_host=args.announce_host, announce_port=args.announce_port,
                collect_timeout=args.collect_timeout, verify_timeout=args.verify_timeout,
                on_ready=ready, settlement_path=args.settlement_ledger,
                credits_per_unit=args.credit_per_unit,
                identity_key=args.identity_key if not args.insecure_legacy else None,
                worker_public_keys=worker_keys,
                encryption_key=args.encryption_key if not args.insecure_legacy else None,
                insecure_legacy=args.insecure_legacy,
            )
            print(json.dumps(info, ensure_ascii=True))
        elif args.cmd == "worker":
            secure_args = (args.identity_key, args.trusted_seed_key, args.encryption_key)
            if args.insecure_legacy:
                if any(secure_args):
                    raise ValueError("do not combine --insecure-legacy with key options")
            elif not all(secure_args):
                raise ValueError(
                    "secure worker mode needs --identity-key, --trusted-seed-key, and "
                    "--encryption-key; for trusted local tests, explicitly pass "
                    "--insecure-legacy"
                )
            body = node.run_worker(
                args.magnet, args.job_dir, args.port, args.seed_port,
                seed_host=args.seed_host, announce_port=args.announce_port,
                announce_timeout=args.announce_timeout,
                fetch_timeout=args.fetch_timeout, result_grace=args.result_grace,
                identity_key=args.identity_key,
                trusted_seed_key=args.trusted_seed_key,
                encryption_key=args.encryption_key,
                allow_custom_executor=args.allow_custom_executor,
                insecure_legacy=args.insecure_legacy,
            )
            print(json.dumps({"worker_port": args.port, "done": sorted(body["units"])}))
        elif args.cmd == "keygen":
            if not args.identity and not args.encryption_key:
                raise ValueError("keygen needs --identity, --encryption-key, or both")
            generated: dict[str, str] = {}
            if args.identity:
                private, public, fingerprint = security.generate_identity(args.identity)
                generated.update(
                    identity_private=str(private),
                    identity_public=str(public),
                    identity_fingerprint=fingerprint,
                )
            if args.encryption_key:
                generated["encryption_key"] = str(
                    security.generate_encryption_key(args.encryption_key)
                )
            print(json.dumps(generated, ensure_ascii=True))
        else:
            print(json.dumps(node.status(args.job_dir), ensure_ascii=True))
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
        _print_error(exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
