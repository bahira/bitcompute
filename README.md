# bitcompute

BitTorrent-style swarm for distributed training & inference compute: one seed
publishes a job manifest over libtorrent, worker peers fetch it, run their
executor units, and the seed aggregates results (median/majority vote).

## Quickstart

Install once:

```
python -m pip install -e ".[dev]"
```

Three terminals (example job in `job/`):

```
python -m bitcompute.cli seed job --port 6881 --workers 6882 6883
```

```
python -m bitcompute.cli worker --magnet 6b248be686ae3b84c00e24501fa08841c442ecaa --job-dir job --port 6882 --seed-port 6881
```

```
python -m bitcompute.cli worker --magnet 6b248be686ae3b84c00e24501fa08841c442ecaa --job-dir job --port 6883 --seed-port 6881
```

Then inspect the outcome:

```
python -m bitcompute.cli status job
```

Tests:

```
python -m pytest
```

## Architecture

| Layer | Role |
| --- | --- |
| torrent | job manifest (`manifest.json`, single file, 16 KiB pieces) |
| piece | chunk of the manifest payload; one piece maps to one work-unit result |
| seed | holds the magnet, serves the manifest, collects `result_<port>.json`, aggregates into `result.bin`/`summary.json` |
| 2 workers | compute peers (default ports 6882/6883), each fetches the manifest, runs its units via the registered executor |
| tit-for-tat | `incentive.Ledger` + `should_unchoke()` rank contributors (net bytes) and order the aggregation |
| verification | every result is also re-fetched as its own torrent (`torrent_verified` in `summary.json`) |

## Discovery modes

- localhost: direct peer injection (`--seed-port`) + DHT bootstrap (`dht_bootstrap_nodes`)
- resume: each worker writes `resume_<port>.dat` via libtorrent; a restarted peer continues from it

## Adding an executor

1. Create `src/bitcompute/executors/<name>.py`.
2. Define a class with a `name` attribute and `run(self, *, unit_uid, shard, params) -> bytes`.
3. Call `register(cls)` from `bitcompute.executor` at module end — `get(name)` lazily imports
   `bitcompute.executors.<name>` and instantiates it.

Reference implementations: `executors/train_numpy.py` (toy linear regression),
`executors/infer_llama.py` (GGUF generation), and the built-in `mock`.

## Skipped (by design)

- tokens (no per-piece token accounting)
- DHT bootstrap nodes configured in-process (127.0.0.1); multi-machine routers = next step
- sandbox (executors run in-process, no isolation)
- pex (no peer-exchange protocol)

## Network state (live data in [`network.json`](network.json))

| Job | Mode | Magnet (info-hash) | Seed port | Workers | Result |
| --- | --- | --- | --- | --- | --- |
| toy-train | train | `6b248be686ae3b84c00e24501fa08841c442ecaa` | 7401 | 2 | w=2.0014, b=0.9711 |
| slm-infer | infer (Qwen2.5-0.5B) | `096509555d5346e4b37573454724aad6b6f62587` | 7411 | 1 | "1+1=2, 2+2=4" |

Torrent verification of every result: all `True` in `network.json`.

## Participer (auto-compute)

Terminal 1 (seed, publie le manifest):

```
python -m bitcompute.cli seed job --port 7401 --workers 7402 7403
```

Terminal 2+3 (workers = participants qui prêtent leur GPU/CPU):

```
python -m bitcompute.cli worker --magnet 6b248be686ae3b84c00e24501fa08841c442ecaa --job-dir job --port 7402 --seed-port 7401
python -m bitcompute.cli worker --magnet 6b248be686ae3b84c00e24501fa08841c442ecaa --job-dir job --port 7403 --seed-port 7401
```

Le seed imprime le JSON d'etat (magnet + `torrent_verified` + median) et écrit
`summary.json` / `result.bin`; `status job` les relit.
