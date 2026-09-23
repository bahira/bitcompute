# bitcompute

BitTorrent-style P2P compute network for distributed ML: a `libtorrent`-backed
swarm distributes content-addressed job manifests; participating nodes
(CPU/GPU) download pieces, run compute units, and publish results back over
the same swarm.

[![CI](https://github.com/bahira/bitcompute/actions/workflows/badge.svg)](https://github.com/bahira/bitcompute/actions)
[![Pages](https://img.shields.io/badge/GitHub%20Pages-live-brightgreen)](https://bahira.github.io/bitcompute/)

## Why

| Need | Answer in this stack |
| --- | --- |
| Content-addressed, piece-verified transport | libtorrent 2.1 (`info_hash_t`, `file_storage`, sha256 pieces) |
| Redundant compute with Byzantine tolerance | per-unit k-redundancy + coordinate-wise `median_vote` |
| Incentives | tit-for-tat ledger (bytes + pieces per peer) |
| Crash resilience | per-port `resume_<port>.dat` files, replayed on restart |
| Determinism | sha256 over canonicalized JSON manifest → stable `job_id` |

## Stack

```
src/bitcompute/
├─ manifest.py     # JobManifest, WorkUnit, deterministic job_id, torrent payload
├─ torrent.py      # libtorrent wrappers: seed_bytes, fetch (DHT bootstrap, resume), wait
├─ node.py         # seed_job / run_worker / status: orchestration, aggregation, verification
├─ cli.py          # `seed`, `worker`, `status` subcommands
├─ executor.py     # Protocol + registry
├─ verify.py       # deterministic hash gate, median_vote, majority_vote
├─ incentive.py    # Ledger (bytes+pieces), should_unchoke (tit-for-tat)
├─ capability.py   # Capability wanted/have
└─ executors/
   ├─ train_numpy.py   # toy SGD trainer (fmt 2d packed vectors)
   └─ infer_llama.py   # GGUF SLM via llama-cpp-python; model id: "owner/repo/file.gguf"
```

The infer executor accepts a `model` param as a Hugging Face id triple (e.g.
`Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q4_k_m.gguf`), cached
once from `hf.co/.../resolve/main/<file>`; or env `BITCOMPUTE_MODEL`; or a
local path. The dashboard (`index.html`) exposes a select box for the
available quantizations and prints the ready-to-use `params` JSON.

## Live network state

See [`network.json`](network.json) — magnets, ports, per-peer token credits
(bytes/pieces), results, and torrent verification (`torrent_verified: true`
for every published result in the last run).

| Job | Mode | Result | Workers | Torrents verified |
| --- | --- | --- | --- | --- |
| toy-train | train | w=2.0029, b=1.0 | 2 | True/True |
| slm-infer | infer | "1+1=2, 2+2=4" | 1 | True |

## Quick start

```
# 1. seed the job (manifest over the swarm)
python -m bitcompute.cli seed job --port 7401 --workers 7402 7403

# 2. N compute peers (any machine, DHT bootstrap via env for multi-machine)
python -m bitcompute.cli worker --magnet <40-hex-magnet> --job-dir job --port 7402 --seed-port 7401
python -m bitcompute.cli worker --magnet <40-hex-magnet> --job-dir job --port 7403 --seed-port 7401

# 3. status (reads summary.json + result.json)
python -m bitcompute.cli status job
```

Multi-machine DHT: `set BITCOMPUTE_DHT_ROUTERS=ip:port;ip:port`.

## Auto-discovery

1. Each session gets `enable_dht: True` + `dht_bootstrap_nodes` (the seed's
   `ip:port`), so the seed announces itself and every later joiner learns it
   from the bootstrap table; without a bootstrap, env `BITCOMPUTE_DHT_ROUTERS`
   (`ip:port;ip:port`) provides the routers.
2. Torrents are added with flags `default | pex-bit`: after the first
   handshake the swarm exchanges peers via PEX, so a 3rd+ worker finds the
   seed without manual injection.
3. `torrent.peer_count(handle)` polls `status().num_peers` — used in tests to
   prove seeders/farmers really connected (≥1 per side, 4/4 green).

## Production notes

- Package `bitcompute 0.1.0` (tag `v0.1.0`), PEP-517, `pip install .` works;
  CI matrix [3.10, 3.11] with pip cache; single-file `dist/bitcompute.exe`.
- Ports: tests use disjoint ranges — torrent 6881-6887, node 6901-6932,
  cli 6971-6992, resume 7440-7462, e2e 7001-7016; in one process each
  session binds its own UDP port, so files can run sequentially without clash.
- Cold starts: `wait` polls until file size == torrent `total_size` (no
  half-flushed json), `_collect` timeout 75 s, worker wait 120 s, 20 s
  announce grace.

## How it works

1. Seed publishes canonicalized manifest as a torrent (pieces = 16 KiB,
   sha256-verified).
2. Each worker fetches the manifest (DHT bootstrap + direct peer injection),
   computes its assigned units locally, writes `result_<port>.json` and
   seeds it as its own torrent (20 s grace).
3. Seed waits for all N result files, verifies each result torrent over the
   swarm (bytes == json + checksum), ranks peers by ledger contribution,
   aggregates: `median_vote` (train, per-coordinate) / `majority_vote`
   (infer), writes `result.bin` + `summary.json` (with `tokens` per peer).

Byzantine results (e.g. 99/99 vs median 2/1) are tolerated by the median at
k≥3; the deterministic gate rejects non-deterministic repeats.

## Tests & benchmarks

```
python -m pytest -q        # 43 passed, localhost-only, no external services
python tools/bench.py      # fetch latencies: ~0.64-0.75 s for 16KB-2MB
python tools/make_dashboard.py   # regenerate index.html from network.json
```

## Roadmap

Closed in v0.1.0: #19 PEX/IPv6 flags + routers, #20 mini-staking tokens,
#21 PyPI packaging + tag, #22 dashboard `index.html`.

## License

MIT
## Clients

- `dist/bitcompute.exe` � one-file console+Tk UI (PyInstaller). Console: same 3 subcommands as
  `python -m bitcompute.cli`; without args it opens the Tk notebook (Seed / Worker / Status tabs,
  HF model select for `params.model`).
