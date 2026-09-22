import json
import os

from bitcompute import node
from bitcompute import torrent as bt


def _write_train_job(jd):
    os.makedirs(jd, exist_ok=True)
    with open(os.path.join(jd, "s1"), "wb") as f:
        f.write("\n".join(f"{x},{2 * x + 1}" for x in range(10)).encode())
    with open(os.path.join(jd, "s2"), "wb") as f:
        f.write("\n".join(f"{x},{2 * x + 1}" for x in range(10, 20)).encode())
    spec = {
        "name": "t", "mode": "train", "shard_files": ["s1", "s2"],
        "units": [{"uid": "r0-s0", "round": 0, "shard": "s1"},
                  {"uid": "r0-s1", "round": 0, "shard": "s2"}],
        "executor": "train_numpy",
        "params": {"lr": 0.01, "steps": 2000}, "redundancy": 1,
    }
    with open(os.path.join(jd, "job.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(spec))


def _write_infer_job(jd):
    os.makedirs(jd, exist_ok=True)
    with open(os.path.join(jd, "q1"), "wb") as f:
        f.write(b"ping")
    spec = {
        "name": "i", "mode": "infer", "shard_files": ["q1"],
        "units": [{"uid": f"i{n}", "round": 0, "input_ref": "q1"} for n in range(3)],
        "executor": "mock", "params": {}, "redundancy": 1,
    }
    with open(os.path.join(jd, "job.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(spec))


def test_train_job_swarm_roundtrip(tmp_path):
    jd = str(tmp_path)
    _write_train_job(jd)
    man = node.load_manifest(jd)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 6901)
    node.run_worker(magnet, jd, 6902, 6901)
    node.run_worker(magnet, jd, 6903, 6901)
    sess.pause()
    summary = node.seed_job(jd, port=6904, worker_ports=(6902, 6903))
    assert abs(summary["w"] - 2.0) < 0.3
    assert abs(summary["b"] - 1.0) < 0.3
    assert summary["workers"] == 2


def test_infer_job_majority_vote(tmp_path):
    jd = str(tmp_path)
    _write_infer_job(jd)
    man = node.load_manifest(jd)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 6905)
    node.run_worker(magnet, jd, 6906, 6905)
    node.run_worker(magnet, jd, 6907, 6905)
    sess.pause()
    summary = node.seed_job(jd, port=6908, worker_ports=(6906, 6907))
    assert summary["mode"] == "infer"
    assert all(v for v in summary["units"].values())
    assert len(summary["units"]) == 3


def test_status_in_progress_then_done(tmp_path):
    jd = str(tmp_path)
    os.makedirs(jd, exist_ok=True)
    assert node.status(jd) == {"state": "in_progress"}
    with open(os.path.join(jd, "summary.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"job_id": "x", "workers": 2}))
    assert node.status(jd)["workers"] == 2
