import json, os, struct
from bitcompute.node import load_manifest, seed_job, run_worker
from bitcompute import torrent as bt, executor as ex

jd = "job"
os.makedirs(jd, exist_ok=True)
with open(os.path.join(jd, "shard-a"), "wb") as f:
    f.write("\n".join(f"{x},{2*x+1}" for x in range(10)).encode())
with open(os.path.join(jd, "shard-b"), "wb") as f:
    f.write("\n".join(f"{x},{2*x+1}" for x in range(10, 20)).encode())
json.dump({"name": "e2e", "mode": "train", "shard_files": ["shard-a", "shard-b"],
           "units": [{"uid": "r0-s0", "round": 0, "shard": "shard-a"},
                     {"uid": "r0-s1", "round": 0, "shard": "shard-b"}],
           "executor": "train_numpy", "params": {"lr": 0.01, "steps": 2000},
           "redundancy": 1},
          open(os.path.join(jd, "job.json"), "w"))
man = load_manifest(jd)
print("job_id:", man.job_id[:16])
_, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 6981)
b1 = run_worker(magnet, jd, 6982, 6981)
b2 = run_worker(magnet, jd, 6983, 6981)
print("worker1:", b1["units"])
print("worker2:", b2["units"])
sess.pause()
summary = seed_job(jd, port=6991, worker_ports=(6982, 6983))
print("summary:", summary)
assert abs(summary["w"] - 2.0) < 0.3 and abs(summary["b"] - 1.0) < 0.3
print("E2E-LOCALHOST OK")
