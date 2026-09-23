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
# 1. seed the job; keep it running and copy the magnet from its "ready" line
python -m bitcompute.cli seed job --port 7401 --workers 7402 7403

# 2. N compute peers (separate terminals or machines; no shared volume needed)
bitcompute worker --magnet <40-hex-info-hash> --job-dir job --port 7402 --seed-host <seed-ip> --seed-port 7401
bitcompute worker --magnet <40-hex-info-hash> --job-dir job --port 7403 --seed-host <seed-ip> --seed-port 7401

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

- PEP 517 package with Python 3.10–3.12 CI on Linux and Windows. Release
  validation builds both wheel and sdist and runs `twine check`.
- Incoming manifests are size-bounded, schema-validated and authenticated by
  recomputing their content-derived `job_id`. Results carry that `job_id`, the
  executor and worker identity, so stale or malformed files are ignored.
- Job file names are constrained to the job directory, writes are atomic, and
  sessions are paused in `finally` blocks. Collection and verification
  timeouts are configurable from the CLI.
- `--seed-host` lets workers fetch from another machine. Workers announce
  results to the seed over HTTP (`seed port + 1000` by default), then the seed
  downloads and verifies each result through its torrent. No shared volume is
  required. Open both the selected TCP/UDP torrent ports and the TCP announce
  port; set `BITCOMPUTE_DHT_ROUTERS` when needed.
- This beta does **not** provide peer identity signatures, sandbox untrusted
  executors, encrypt payloads, or implement payment settlement. Only run
  executors shipped by a trusted installation and do not put secrets in jobs.

## How it works

1. Seed publishes canonicalized manifest as a torrent (pieces = 16 KiB,
   sha256-verified).
2. Each worker fetches the manifest (DHT bootstrap + direct peer injection),
   computes its assigned units locally, seeds `result_<port>.json` as a new
   torrent, then announces its job ID and result to the seed's HTTP control
   plane (20 s torrent grace by default).
3. The seed validates each announcement, immediately fetches the announced
   result torrent from that worker, checks the bytes/content address, and
   ranks verified peers by ledger contribution before aggregation:
   aggregates: `median_vote` (train, per-coordinate) / `majority_vote`
   (infer), writes `result.bin` + `summary.json` (with `tokens` per peer).

Byzantine numeric results (e.g. 99/99 vs median 2/1) are tolerated when fewer
than half of the verified replicas are malicious. Content addressing detects
transport tampering; it does not prove that a remote executor is trustworthy.

## Tests & benchmarks

```
python -m pytest -q        # localhost integration tests; no external services
python tools/bench.py      # fetch latencies: ~0.64-0.75 s for 16KB-2MB
python tools/make_dashboard.py   # regenerate index.html from network.json
```

## Roadmap

Closed in v0.1.0: #19 PEX/IPv6 flags + routers, #20 mini-staking tokens,
#21 PyPI packaging + tag, #22 dashboard `index.html`.

## License

MIT
## Desktop client

`python client.py` starts the optional Tk interface (Seed / Worker / Status
 tabs). Build distributable clients from the current source with PyInstaller;
generated binaries are intentionally not committed to the repository.
