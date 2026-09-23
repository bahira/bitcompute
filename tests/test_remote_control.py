import json
import threading

from bitcompute import node


def test_remote_worker_returns_result_without_shared_job_dir(tmp_path):
    seed_dir = tmp_path / "seed"
    worker_dir = tmp_path / "worker"
    seed_dir.mkdir()
    worker_dir.mkdir()
    (seed_dir / "data.csv").write_bytes(b"0,1\n1,3\n2,5\n")
    (seed_dir / "job.json").write_text(json.dumps({
        "name": "remote-control",
        "mode": "train",
        "shard_files": ["data.csv"],
        "units": [{"uid": "u1", "round": 0, "shard": "data.csv"}],
        "executor": "train_numpy",
        "params": {"steps": 100, "lr": 0.01},
        "redundancy": 1,
    }))

    workers = []

    def ready(event):
        worker = threading.Thread(
            target=node.run_worker,
            args=(event["magnet"], str(worker_dir), 7022, 7021),
            kwargs={"announce_port": event["announce_port"], "result_grace": 10},
        )
        worker.start()
        workers.append(worker)

    summary = node.seed_job(
        str(seed_dir), port=7021, worker_ports=(7022,),
        announce_port=8021, collect_timeout=45, verify_timeout=20,
        on_ready=ready,
    )
    for worker in workers:
        worker.join(30)

    assert summary["workers"] == 1
    assert summary["torrent_verified"] == {7022: True}
    assert not (seed_dir / "result_7022.json").exists()
    assert (worker_dir / "result_7022.json").exists()
