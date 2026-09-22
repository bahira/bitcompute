import json
import os
import threading

from bitcompute import node
from bitcompute import torrent as bt


def _write_train_job(jd, rounds=1):
    os.makedirs(jd, exist_ok=True)
    with open(os.path.join(jd, "s1"), "wb") as f:
        f.write("\n".join(f"{x},{2 * x + 1}" for x in range(10)).encode())
    with open(os.path.join(jd, "s2"), "wb") as f:
        f.write("\n".join(f"{x},{2 * x + 1}" for x in range(10, 20)).encode())
    units = []
    for r in range(rounds):
        units.append({"uid": f"r{r}-s0", "round": r, "shard": "s1"})
        units.append({"uid": f"r{r}-s1", "round": r, "shard": "s2"})
    spec = {
        "name": "t", "mode": "train", "shard_files": ["s1", "s2"],
        "units": units, "executor": "train_numpy",
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


def _start_workers(magnet, jd, base):
    return [threading.Thread(target=node.run_worker,
                             args=(magnet, jd, base + i + 1, base),
                             name=f"w{base + i + 1}")
            for i in range(2)]


def _join(threads):
    for t in threads:
        t.join(40)


def test_train_job_swarm_roundtrip(tmp_path):
    jd = str(tmp_path)
    _write_train_job(jd)
    man = node.load_manifest(jd)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 6901)
    th = _start_workers(magnet, jd, 6901)
    [t.start() for t in th]
    summary = node.seed_job(jd, port=6910, worker_ports=(6902, 6903))
    sess.pause()
    _join(th)
    assert abs(summary["w"] - 2.0) < 0.3
    assert abs(summary["b"] - 1.0) < 0.3
    assert summary["workers"] == 2
    assert all(summary["torrent_verified"].values())


def test_train_job_two_rounds(tmp_path):
    jd = str(tmp_path)
    _write_train_job(jd, rounds=2)
    man = node.load_manifest(jd)
    assert man.rounds() == [0, 1]
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 6931)
    th = _start_workers(magnet, jd, 6931)
    [t.start() for t in th]
    summary = node.seed_job(jd, port=6940, worker_ports=(6932, 6933))
    sess.pause()
    _join(th)
    assert abs(summary["w"] - 2.0) < 0.3
    assert abs(summary["b"] - 1.0) < 0.3
    results = [json.loads(open(os.path.join(jd, f"result_{p}.json")).read())
               for p in (6932, 6933)]
    assert sorted(results[0]["units"]) == ["r0-s0", "r0-s1", "r1-s0", "r1-s1"]
    assert all(summary["torrent_verified"].values())


def test_infer_job_majority_vote(tmp_path):
    jd = str(tmp_path)
    _write_infer_job(jd)
    man = node.load_manifest(jd)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 6951)
    th = _start_workers(magnet, jd, 6951)
    [t.start() for t in th]
    summary = node.seed_job(jd, port=6960, worker_ports=(6952, 6953))
    sess.pause()
    _join(th)
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


