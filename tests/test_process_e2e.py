from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from bitcompute import security


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def _wait_for_ready(process: subprocess.Popen[str]) -> dict:
    assert process.stderr is not None
    for line in iter(process.stderr.readline, ""):
        if line.startswith("bitcompute: ready "):
            return json.loads(line.removeprefix("bitcompute: ready "))
        if process.poll() is not None:
            break
    stdout = process.stdout.read() if process.stdout else ""
    stderr = process.stderr.read() if process.stderr else ""
    raise AssertionError(
        f"seed exited before ready (code {process.poll()})\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )


def test_seed_and_worker_in_separate_processes(tmp_path):
    """Exercise real TCP/UDP sessions and HTTP result delivery across processes."""
    repo = Path(__file__).resolve().parents[1]
    seed_dir = tmp_path / "seed"
    worker_dir = tmp_path / "worker"
    seed_dir.mkdir()
    worker_dir.mkdir()
    (seed_dir / "job.json").write_text(json.dumps({
        "name": "separate-process-smoke",
        "mode": "infer",
        "shard_files": [],
        "units": [{"uid": "u0", "round": 0, "input_ref": "ping"}],
        "executor": "mock",
        "params": {},
        "redundancy": 1,
    }), encoding="utf-8")

    seed_port, worker_port, announce_port = _free_tcp_port(), _free_tcp_port(), _free_tcp_port()
    while len({seed_port, worker_port, announce_port}) != 3:
        seed_port, worker_port, announce_port = _free_tcp_port(), _free_tcp_port(), _free_tcp_port()
    seed_private, seed_public, seed_fingerprint = security.generate_identity(
        tmp_path / "seed.key"
    )
    worker_private, worker_public, _ = security.generate_identity(tmp_path / "worker.key")
    shared_key = security.generate_encryption_key(tmp_path / "job.aes")

    seed = subprocess.Popen(
        [
            sys.executable, "-m", "bitcompute.cli", "seed", str(seed_dir),
            "--port", str(seed_port), "--workers", str(worker_port),
            "--announce-host", "127.0.0.1", "--announce-port", str(announce_port),
            "--collect-timeout", "40", "--verify-timeout", "20",
            "--identity-key", str(seed_private), "--encryption-key", str(shared_key),
            "--worker-key", f"{worker_port}={worker_public}",
        ],
        cwd=repo,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    worker = None
    try:
        ready = _wait_for_ready(seed)
        assert ready["seed_fingerprint"] == seed_fingerprint
        worker = subprocess.Popen(
            [
                sys.executable, "-m", "bitcompute.cli", "worker",
                "--magnet", ready["magnet"], "--job-dir", str(worker_dir),
                "--port", str(worker_port), "--seed-host", "127.0.0.1",
                "--seed-port", str(seed_port), "--announce-port", str(announce_port),
                "--announce-timeout", "8", "--fetch-timeout", "30", "--result-grace", "1",
                "--identity-key", str(worker_private),
                "--trusted-seed-key", str(seed_public),
                "--encryption-key", str(shared_key),
            ],
            cwd=repo,
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        worker_stdout, worker_stderr = worker.communicate(timeout=45)
        assert worker.returncode == 0, f"worker failed\n{worker_stdout}\n{worker_stderr}"
        seed_stdout, seed_stderr = seed.communicate(timeout=45)
        assert seed.returncode == 0, f"seed failed\n{seed_stdout}\n{seed_stderr}"
        summary = json.loads(seed_stdout)
        assert summary["workers"] == 1
        assert summary["torrent_verified"] == {str(worker_port): True}
        assert summary["units"]["u0"]
        receipt = summary["settlement"]["receipts"][0]
        assert receipt["status"] == "credit-recorded"
        assert receipt["worker_id"] == security.key_fingerprint(
            security.load_public_key(worker_public)
        )
        assert not (seed_dir / f"result_{worker_port}.json").exists()
        assert (worker_dir / f"result_{worker_port}.json").is_file()
    finally:
        if worker is not None and worker.poll() is None:
            worker.kill()
            worker.communicate(timeout=5)
        if seed.poll() is None:
            seed.kill()
            seed.communicate(timeout=5)
