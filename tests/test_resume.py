import json
import os
import struct
import threading
import time

from bitcompute import node
from bitcompute import torrent as bt


def _write_train_job(jd, steps=500):
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
        "params": {"lr": 0.01, "steps": steps}, "redundancy": 1,
    }
    with open(os.path.join(jd, "job.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(spec))


def _workers(magnet, jd, base, n):
    return [threading.Thread(target=node.run_worker,
                             args=(magnet, jd, base + i + 1, base),
                             name=f"w{base + i + 1}")
            for i in range(n)]


def _join(threads):
    for t in threads:
        t.join(45)


def test_resume_roundtrip(tmp_path):
    jd = str(tmp_path)
    _write_train_job(jd, steps=500)
    man = node.load_manifest(jd)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 7440)
    th = _workers(magnet, jd, 7440, 2)
    [t.start() for t in th]
    time.sleep(1)
    node.seed_job(jd, port=7448, worker_ports=(7441, 7442))
    _join(th)
    for wp in (7441, 7442):
        rp = os.path.join(jd, f"resume_{wp}.dat")
        assert os.path.isfile(rp), rp
        assert os.path.getsize(rp) > 0
    # second run: same worker port + seed still alive → resume loads, completes
    for wp in (7441, 7442):
        body = node.run_worker(magnet, jd, wp, 7440)
        assert body["units"]
        assert set(body["units"]) == {"r0-s0", "r0-s1"}
    sess.pause()


def test_redundancy_three_workers(tmp_path):
    jd = str(tmp_path)
    _write_train_job(jd, steps=500)
    man = node.load_manifest(jd)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 7450)
    th = _workers(magnet, jd, 7450, 3)
    [t.start() for t in th]
    time.sleep(1)
    summary = node.seed_job(jd, port=7458, worker_ports=(7451, 7452, 7453))
    sess.pause()
    _join(th)
    assert summary["workers"] == 3
    assert abs(summary["w"] - 2.0) < 0.3
    assert abs(summary["b"] - 1.0) < 0.3
    assert all(summary["torrent_verified"].values())


def test_byzantine_excluded(tmp_path):
    jd = str(tmp_path)
    _write_train_job(jd, steps=500)
    man = node.load_manifest(jd)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 7450)
    th = _workers(magnet, jd, 7450, 3)
    [t.start() for t in th]
    time.sleep(1)
    liar = struct.pack("<2d", 99.0, 99.0).hex()
    fake = {"worker_port": 7454, "executor": "train_numpy",
            "units": {"r0-s0": liar, "r0-s1": liar}}
    with open(os.path.join(jd, "result_7454.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(fake))
    summary = node.seed_job(jd, port=7458, worker_ports=(7451, 7452, 7453))
    sess.pause()
    _join(th)
    # byzantine (7454) not in worker_ports → excluded; median of 3 honest ≈ 2.0
    assert abs(summary["w"] - 2.0) < 0.3
    assert summary["workers"] == 3
