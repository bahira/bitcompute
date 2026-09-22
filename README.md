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
| tit-for-tat | libtorrent's piece-exchange fairness rule governing peer uploads |

## Adding an executor

1. Create `src/bitcompute/executors/<name>.py`.
2. Define a class with a `name` attribute and `run(self, *, unit_uid, shard, params) -> bytes`.
3. Call `register(cls)` from `bitcompute.executor` at module end — `get(name)` lazily imports
   `bitcompute.executors.<name>` and instantiates it.

Reference implementations: `executors/train_numpy.py` (toy linear regression),
`executors/infer_llama.py` (GGUF generation), and the built-in `mock`.

## Skipped (by design)

- tokens (no per-piece token accounting)
- DHT (disabled; direct peer injection only)
- sandbox (executors run in-process, no isolation)
- pex (no peer-exchange protocol)
