"""Swarm orchestration: seed a job, work as a compute peer, aggregate results."""
from __future__ import annotations

import json
import os
import struct
import time

from bitcompute import torrent as bt
from bitcompute.capability import Capability
from bitcompute.executor import get as get_executor
from bitcompute.incentive import Ledger, should_unchoke
from bitcompute.manifest import JobManifest, WorkUnit
from bitcompute.verify import majority_vote, median_vote


def _fmt_for(executor: str) -> str | None:
    return {"train_numpy": "2d", "mock": "2d"}.get(executor)


def load_manifest(job_dir: str) -> JobManifest:
    with open(os.path.join(job_dir, "job.json"), encoding="utf-8") as f:
        spec = json.loads(f.read())
    shards = []
    for name in spec["shard_files"]:
        with open(os.path.join(job_dir, name), "rb") as f:
            shards.append(f.read())
    units = [WorkUnit(**u) for u in spec["units"]]
    return JobManifest.create(
        name=spec["name"], mode=spec["mode"], shards=shards, units=units,
        executor=spec["executor"], params=spec["params"],
        redundancy=spec.get("redundancy", 1),
        shard_names=spec.get("shard_files", []),
    )


def run_worker(magnet: str, job_dir: str, port: int, seed_port: int) -> dict:
    """Join the swarm, compute this node's units, publish results. Returns payload dict."""
    dest = os.path.join(job_dir, f"w{port}")
    os.makedirs(dest, exist_ok=True)
    bootstrap = f"127.0.0.1:{seed_port}"
    resume = os.path.join(job_dir, f"resume_{port}.dat")
    handle, sess = bt.fetch(magnet, dest, port, seed_port=seed_port,
                            bootstrap=bootstrap, resume_path=resume)
    if not bt.wait(handle, timeout=120):
        raise RuntimeError(f"manifest not fetched in time (port {port})")
    bt.save_resume(handle, resume)
    payload = json.loads(bt.read_result(handle).decode())
    shards = dict(zip(payload["shard_names"],
                      (bytes.fromhex(h) for h in payload["shards"])))
    units = [WorkUnit(**u) for u in payload["units"]]
    executor = get_executor(payload["executor"])
    cap = Capability(job_id=payload["job_id"], executors=[payload["executor"]])
    out: dict[str, str] = {}
    for u in units:
        if not cap.wants(u.uid):
            continue
        key = u.shard or u.input_ref or ""
        shard = shards.get(key, b"")
        result = executor.run(unit_uid=u.uid, shard=shard, params=payload["params"])
        out[u.uid] = result.hex()
        cap.have.add(u.uid)
    body = {"worker_port": port, "executor": payload["executor"], "units": out}
    blob = json.dumps(body).encode()
    with open(os.path.join(job_dir, f"result_{port}.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(body))
    # ponytail: 20s grace so the seed can pull our result torrent before we exit
    bt.seed_in_session(sess, blob, f"result_{port}.json")
    time.sleep(20)
    sess.pause()
    return body


def _collect(job_dir: str, worker_ports: tuple[int, ...], timeout: float = 75.0) -> list[dict]:
    t0 = time.time()
    got: dict[int, dict] = {}
    while time.time() - t0 < timeout and len(got) < len(worker_ports):
        for wp in worker_ports:
            if wp in got:
                continue
            p = os.path.join(job_dir, f"result_{wp}.json")
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    got[wp] = json.loads(f.read())
        time.sleep(0.2)
    return list(got.values())


def _verify_result_torrents(job_dir: str, magnet: str, results: list[dict],
                             seed_port: int, fetch_sess) -> dict[int, bool]:
    """Fetch each worker's result torrent over the swarm; True if bytes match disk."""
    verified: dict[int, bool] = {}
    for k, r in enumerate(results):
        wp = r["worker_port"]
        blob = json.dumps(r).encode()
        hex_ = bt.hash_of(blob, f"result_{wp}.json")
        dest = job_dir
        path = os.path.join(dest, f"result_{wp}.json")
        sess = bt.lt.session(bt._settings(seed_port + 1 + k))
        h2, _ = bt.fetch(hex_, dest, wp, seed_port=wp,
                         name=f"result_{wp}.json", session=sess)
        ok = bt.wait(h2, timeout=25)
        data = bt.read_result(h2) if ok else b""
        sess.pause()
        verified[wp] = ok and data == blob and bt.checksum(data) == bt.checksum(blob)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                verified[wp] = verified[wp] and json.loads(f.read()) == r
    return verified


def seed_job(job_dir: str, port: int = 6881,
             worker_ports: tuple[int, ...] = (6882, 6883)) -> dict:
    """Seed manifest, gather worker results, aggregate, write result.bin/result.json."""
    man = load_manifest(job_dir)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", port)
    results = _collect(job_dir, worker_ports)
    time.sleep(4)  # ponytail: let worker sessions announce result torrents (2 sessions cold = ~2-3s each)
    led = Ledger()
    for r in results:
        led.record(str(r["worker_port"]),
                   contributed=os.path.getsize(
                       os.path.join(job_dir, f"result_{r['worker_port']}.json")),
                   received=1)
    ranked = should_unchoke(led, max_unchoked=max(1, len(results)))
    results.sort(key=lambda r: (ranked.index(str(r["worker_port"]))
                                if str(r["worker_port"]) in ranked else 99,
                                r["worker_port"]))
    tokens = {str(r["worker_port"]): {
        "bytes": os.path.getsize(os.path.join(job_dir, f"result_{r['worker_port']}.json")),
        "pieces": len(r["units"]),
    } for r in results}
    torrent_ok = _verify_result_torrents(job_dir, magnet, results, port, None)
    if man.mode == "train":
        packed = [bytes.fromhex(h) for r in results for h in r["units"].values()]
        if not packed:
            raise RuntimeError("no results to aggregate")
        fmt = _fmt_for(man.executor) or "2d"
        final = median_vote(packed, fmt)
        w, b = struct.unpack("<" + fmt, final)
        with open(os.path.join(job_dir, "result.bin"), "wb") as f:
            f.write(final)
        summary = {"job_id": man.job_id, "mode": "train", "w": w, "b": b,
                   "workers": len(results)}
    else:
        uid_order = [u.uid for u in man.units]
        merged_infer: dict[str, str] = {}
        for uid in uid_order:
            vals = [bytes.fromhex(r["units"][uid]) for r in results if uid in r["units"]]
            if vals:
                merged_infer[uid] = majority_vote(vals).decode("latin-1")
        with open(os.path.join(job_dir, "result.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(merged_infer))
        summary = {"job_id": man.job_id, "mode": "infer", "units": merged_infer,
                   "workers": len(results)}
    summary["torrent_verified"] = torrent_ok
    summary["tokens"] = tokens
    summary["magnet"] = magnet
    sess.pause()
    with open(os.path.join(job_dir, "summary.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(summary))
    return summary


def status(job_dir: str) -> dict:
    p = os.path.join(job_dir, "summary.json")
    if not os.path.isfile(p):
        return {"state": "in_progress"}
    with open(p, encoding="utf-8") as f:
        return json.loads(f.read())

