import json
import os
import struct
import subprocess
import sys
import threading

from bitcompute import node
from bitcompute import torrent as bt


def test_e2e_train_three_nodes(tmp_path):
    jd = str(tmp_path / "job")
    os.makedirs(jd, exist_ok=True)
    with open(os.path.join(jd, "shard-a"), "wb") as f:
        f.write("\n".join(f"{x},{2*x+1}" for x in range(10)).encode())
    with open(os.path.join(jd, "shard-b"), "wb") as f:
        f.write("\n".join(f"{x},{2*x+1}" for x in range(10, 20)).encode())
    spec = {"name": "e2e", "mode": "train", "shard_files": ["shard-a", "shard-b"],
            "units": [{"uid": "r0-s0", "round": 0, "shard": "shard-a"},
                      {"uid": "r0-s1", "round": 0, "shard": "shard-b"}],
            "executor": "train_numpy", "params": {"lr": 0.01, "steps": 2000},
            "redundancy": 1}
    with open(os.path.join(jd, "job.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(spec))

    # seed holds the magnet; workers fetch from it while it is alive (ports 7001-7004)
    man = node.load_manifest(jd)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 7001)
    th = [
        threading.Thread(
            target=node.run_worker, args=(magnet, jd, 7002, 7001),
            kwargs={"insecure_legacy": True},
        ),
        threading.Thread(
            target=node.run_worker, args=(magnet, jd, 7003, 7001),
            kwargs={"insecure_legacy": True},
        ),
    ]
    [t.start() for t in th]
    summary = node.seed_job(
        jd, port=7004, worker_ports=(7002, 7003), insecure_legacy=True
    )
    sess.pause()
    [t.join(40) for t in th]

    assert abs(summary["w"] - 2.0) < 0.3
    assert abs(summary["b"] - 1.0) < 0.3
    assert summary["workers"] == 2
    assert all(summary["torrent_verified"].values())
    assert summary["torrent_verified"] == {7002: True, 7003: True}

    raw = open(os.path.join(jd, "result.bin"), "rb").read()
    assert len(raw) == 16
    w, b = struct.unpack("<2d", raw)
    assert abs(w - summary["w"]) < 1e-9 and abs(b - summary["b"]) < 1e-9

    saved = json.loads(open(os.path.join(jd, "summary.json"), encoding="utf-8").read())
    assert saved["workers"] == 2 and abs(saved["w"] - summary["w"]) < 1e-9

    proc = subprocess.run([sys.executable, "-m", "bitcompute.cli", "status", jd],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    st = json.loads(proc.stdout)
    assert st["workers"] == 2 and abs(st["w"] - summary["w"]) < 1e-9


def test_e2e_infer_three_nodes(tmp_path):
    jd = str(tmp_path / "job-infer")
    os.makedirs(jd, exist_ok=True)
    spec = {"name": "infer-demo", "mode": "infer", "shard_files": [],
            "units": [{"uid": "i0", "round": 0, "input_ref": "ping"},
                      {"uid": "i1", "round": 0, "input_ref": "ping"},
                      {"uid": "i2", "round": 0, "input_ref": "ping"}],
            "executor": "mock", "params": {}, "redundancy": 1}
    with open(os.path.join(jd, "job.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(spec))

    man = node.load_manifest(jd)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 7011)
    th = [
        threading.Thread(
            target=node.run_worker, args=(magnet, jd, 7012, 7011),
            kwargs={"insecure_legacy": True},
        ),
        threading.Thread(
            target=node.run_worker, args=(magnet, jd, 7013, 7011),
            kwargs={"insecure_legacy": True},
        ),
    ]
    [t.start() for t in th]
    summary = node.seed_job(
        jd, port=7014, worker_ports=(7012, 7013), insecure_legacy=True
    )
    sess.pause()
    [t.join(40) for t in th]

    assert summary["workers"] == 2
    assert all(summary["torrent_verified"].values())
    units = summary["units"]
    assert len(units) == 3
    assert all(isinstance(v, str) and v for v in units.values())
    assert sorted(units) == ["i0", "i1", "i2"]
