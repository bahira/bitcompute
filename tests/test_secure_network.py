from __future__ import annotations

import json
import socket
import threading

from bitcompute import node, security


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_signed_encrypted_job_and_result_end_to_end(tmp_path):
    seed_private, seed_public, _ = security.generate_identity(tmp_path / "seed.key")
    worker_private, worker_public, _ = security.generate_identity(tmp_path / "worker.key")
    shared_key = security.generate_encryption_key(tmp_path / "job.aes")

    seed_dir = tmp_path / "seed"
    worker_dir = tmp_path / "worker"
    seed_dir.mkdir()
    worker_dir.mkdir()
    (seed_dir / "job.json").write_text(json.dumps({
        "name": "secure-smoke",
        "mode": "infer",
        "shard_files": [],
        "units": [{"uid": "u0", "round": 0, "input_ref": "ping"}],
        "executor": "mock",
        "params": {},
        "redundancy": 1,
    }), encoding="utf-8")

    seed_port, worker_port, announce_port = _free_port(), _free_port(), _free_port()
    while len({seed_port, worker_port, announce_port}) != 3:
        seed_port, worker_port, announce_port = _free_port(), _free_port(), _free_port()
    workers: list[threading.Thread] = []
    worker_errors: list[BaseException] = []

    def ready(event: dict) -> None:
        def work() -> None:
            try:
                node.run_worker(
                    event["magnet"], str(worker_dir), worker_port, seed_port,
                    seed_host="127.0.0.1", announce_port=event["announce_port"],
                    announce_timeout=8, fetch_timeout=20, result_grace=4,
                    identity_key=worker_private,
                    trusted_seed_key=seed_public,
                    encryption_key=shared_key,
                )
            except BaseException as exc:  # surfaced in the main test thread
                worker_errors.append(exc)

        thread = threading.Thread(target=work)
        thread.start()
        workers.append(thread)

    summary = node.seed_job(
        str(seed_dir), port=seed_port, worker_ports=(worker_port,),
        announce_host="127.0.0.1", announce_port=announce_port,
        collect_timeout=25, verify_timeout=15, on_ready=ready,
        identity_key=seed_private,
        worker_public_keys={worker_port: worker_public},
        encryption_key=shared_key,
    )
    for worker in workers:
        worker.join(timeout=15)
        assert not worker.is_alive()
    assert not worker_errors
    assert summary["workers"] == 1
    assert summary["torrent_verified"] == {worker_port: True}
    assert summary["units"]["u0"]

    result_wire = (worker_dir / f"result_{worker_port}.json").read_bytes()
    assert b'"encrypted":true' in result_wire
    assert b'"units"' not in result_wire
