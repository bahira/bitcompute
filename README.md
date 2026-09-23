# bitcompute

BitTorrent-style P2P compute network for distributed ML. A coordinator publishes a content-addressed job; trusted worker nodes download it, execute bounded work units, and publish verifiable results back over the swarm.

[![CI](https://github.com/bahira/bitcompute/actions/workflows/badge.svg)](https://github.com/bahira/bitcompute/actions)
[![Pages](https://img.shields.io/badge/GitHub%20Pages-live-brightgreen)](https://bahira.github.io/bitcompute/)

## What is implemented

| Area | Implementation |
| --- | --- |
| Transport | libtorrent 2.1, content-addressed torrents, piece verification, DHT + PEX |
| Job integrity | Canonical JSON manifest and SHA-256-derived `job_id` |
| Result robustness | Redundant work, coordinate median for numeric training, majority vote for inference |
| Crash recovery | Atomic fast-resume files; resume-data alert API |
| Node authentication | Pinned Ed25519 signatures on secure-mode manifests and worker results |
| Payload privacy | AES-256-GCM encrypted manifest/results in secure mode; key is shared out of band |
| Credit accounting | Durable, idempotent SQLite receipts for verified work; optional provider adapter API |
| Execution policy | Built-in executor allowlist; opt-in custom plugins run in a fail-closed bubblewrap sandbox on Linux |

## Components

```
src/bitcompute/
├─ manifest.py     # schema validation, deterministic job_id, torrent payload
├─ torrent.py      # libtorrent 2.1 APIs, DHT, peer discovery, resume
├─ node.py         # seed / worker / result control-plane orchestration
├─ cli.py          # secure-by-default seed, worker, status, keygen commands
├─ security.py     # Ed25519 signed envelopes + AES-GCM payload encryption
├─ settlement.py   # durable credit receipts + PaymentProvider interface
├─ executor.py     # static built-in allowlist + installed-plugin entry-point lookup
├─ sandbox.py      # fail-closed, resource-limited bubblewrap launcher
├─ sandbox_runner.py # child-process plugin loader and bounded result protocol
├─ verify.py       # deterministic hashes, median_vote, majority_vote
└─ executors/
   ├─ train_numpy.py   # bounded toy SGD trainer
   └─ infer_llama.py   # GGUF SLM via llama-cpp-python
```

The `infer_llama` executor accepts a local GGUF path, `BITCOMPUTE_MODEL`, or a Hugging Face `owner/repository/file.gguf` identifier. Downloaded models are cached under `BITCOMPUTE_MODEL_DIR` or `~/.cache/bitcompute/models`.

## Secure multi-machine quick start

Secure mode requires each worker to have its own Ed25519 identity key. The seed pins each worker's public key; each worker pins the seed's public key. A 32-byte AES key is shared only with authorized job participants, using a separate trusted channel. **Do not send private keys or the AES key through the swarm, and do not commit them.**

### 1. Generate keys

On the coordinator:

```sh
mkdir -p keys
bitcompute keygen --identity keys/seed.key --encryption-key keys/job.aes
```

On each worker, using distinct filenames:

```sh
bitcompute keygen --identity keys/worker-1.key
bitcompute keygen --identity keys/worker-2.key
```

Exchange public keys and the job key out of band:

- Copy `seed.key.pub` to each worker and verify the seed fingerprint shown in the coordinator's `ready` event.
- Copy each `worker-N.key.pub` to the coordinator and associate it with that worker's listening port.
- Securely provision `job.aes` to the authorized workers only. All participants in one job share this key; rotate it for each new trust group/job.

Private keys are written with owner-only permissions on POSIX systems. Keep backups in a secure secrets store.

### 2. Start the coordinator

```sh
bitcompute seed job --port 7401 --workers 7402 7403 \
  --announce-host 0.0.0.0 --announce-port 8401 \
  --identity-key keys/seed.key --encryption-key keys/job.aes \
  --worker-key 7402=keys/worker-1.key.pub \
  --worker-key 7403=keys/worker-2.key.pub
```

Copy the `magnet` from the `bitcompute: ready` line to the workers. The event also prints a seed-key fingerprint for out-of-band confirmation.

### 3. Join from each worker machine

```sh
bitcompute worker --magnet <40-hex-info-hash> --job-dir worker-job \
  --port 7402 --seed-host <coordinator-public-host> --seed-port 7401 \
  --announce-port 8401 \
  --identity-key keys/worker-1.key \
  --trusted-seed-key keys/seed.key.pub \
  --encryption-key keys/job.aes
```

Use the corresponding worker identity and port for each worker. No shared job directory is required. Check the coordinator with `bitcompute status job`.

### Firewall and NAT requirements

- Coordinator: allow inbound **TCP and UDP** on the selected BitTorrent port (e.g. `7401`) and inbound **TCP** on the result-control port (e.g. `8401`).
- Each worker: allow inbound **TCP and UDP** on its BitTorrent port (e.g. `7402`), because the coordinator fetches the result torrent directly from that worker.
- Permit outbound DHT UDP and worker-to-coordinator TCP on the announce port. If a worker is behind NAT, forward its BitTorrent port and use a reachable public address; a closed inbound worker port prevents result-torrent verification.
- Public DHT routers are configured by default. Override them with `BITCOMPUTE_DHT_ROUTERS` as a comma- or semicolon-separated list of `host:port` pairs. Direct seed injection is also used for the initial manifest fetch.

A localhost, separate-process test exercises the same TCP/UDP and HTTP paths. External DHT discovery is tested separately with `BITCOMPUTE_TEST_PUBLIC_DHT=1`; it needs outbound UDP and reachable public routers, which some CI/sandbox networks block.

### Legacy local-only mode

For an isolated trusted development setup only, both commands can opt out of signatures and encryption with `--insecure-legacy`. Direct Python callers must opt in separately with `node.seed_job(..., insecure_legacy=True)` and `node.run_worker(..., insecure_legacy=True)`; omitting keys no longer silently selects legacy mode. Do not use this mode on a public or otherwise untrusted network.

## Inference with llama-cpp and a real GGUF

Install the optional backend and point the test at a local GGUF:

```sh
python -m pip install '.[llm]'
BITCOMPUTE_TEST_GGUF=/path/to/model.gguf python -m pytest -q tests/test_infer_llama.py
```

CI has a dedicated CPU inference job: it builds `llama-cpp-python`, downloads a pinned 105 MB SmolLM GGUF, verifies its SHA-256, and runs the real model. The ordinary unit-test job does not download models.

## Executor trust and resource limits

Workers use a fixed allowlist for the bundled `mock`, `train_numpy`, and `infer_llama` executors; job manifests cannot supply arbitrary Python import paths. Custom packages must be installed on the worker and expose an entry point in the `bitcompute.executors` group, for example:

```toml
[project.entry-points."bitcompute.executors"]
my_executor = "my_package.executor:MyExecutor"
```

They are disabled unless the operator passes `--allow-custom-executor`. With that opt-in, Bitcompute resolves only the installed entry-point metadata in the worker process and imports/runs the plugin in a separate `bubblewrap` process. On Linux, the child has isolated user, PID, IPC, UTS and network namespaces; a read-only view of the active Python environment and Bitcompute source; a temporary writable `/tmp`; a 30-second CPU limit, 1 GiB address-space limit, 64 MiB per-file limit, and a 35-second wall-clock timeout. Plugin stdout/stderr are discarded. If bubblewrap is unavailable or cannot create the sandbox, the worker fails closed; custom plugins are currently unsupported on non-Linux systems. Install `bubblewrap` through the worker OS package manager before opting in.

This boundary reduces plugin access to the host, but is not a VM: do not run hostile code on a machine holding valuable secrets, and keep the host kernel, bubblewrap, Python environment, plugin packages and native libraries patched and trusted. The sandbox cannot contain vulnerabilities in those components. Bundled executors are first-party code and continue to run in-process; the built-in SGD executor rejects oversized inputs and enforces a work budget. Python integrations that explicitly call `executor.register()` must import that trusted class in their own process; prefer installed entry points so third-party module code is first imported only inside the sandbox.

## Credit records and external payment

After results pass signature and torrent verification, the coordinator writes idempotent credit receipts to `job/settlement.sqlite3` by default. Each verified worker-unit result earns `1` internal `bitcompute-credit` unless changed with `--credit-per-unit`; use `--settlement-ledger PATH` to choose another SQLite file. Secure-mode receipts are signed by the coordinator.

This is **internal credit accounting, not money movement**. `settlement.PaymentProvider` is an adapter interface; no Stripe, Lightning, bank, or blockchain rail is selected or called automatically. A provider must define conversion, custody, recipient onboarding, fees, and idempotency before actual payouts can be enabled. Provider credentials belong in a secrets manager/environment, never in jobs or this repository.

## Live sample data

[`network.json`](network.json) and the `job/` / `job_inf/` folders are checked-in sample snapshots from prior runs; they are not a live view of currently running peers.

| Sample job | Mode | Recorded result | Workers |
| --- | --- | --- | ---: |
| toy-train | train | `w=2.0029, b=1.0` | 2 |
| slm-infer | infer | `1+1=2, 2+2=4` | 1 |

## Development checks

```sh
python -m pip install -e ".[dev]"
ruff check src
python -m pytest -q
python -m build
twine check dist/*
```

The suite includes localhost integration, separate-process networking, secure signed/encrypted result delivery, settlement idempotency, and resume tests. To exercise public DHT routing on a network that permits it:

```sh
BITCOMPUTE_TEST_PUBLIC_DHT=1 python -m pytest -q tests/test_torrent.py::test_public_dht_only_discovery
```

## Roadmap and limitations

Closed in v0.1.0: #19 PEX/IPv6 flags + routers, #20 mini-staking tokens, #21 PyPI packaging + tag, #22 dashboard `index.html`; #23 adds the result control plane.

The secure protocol authenticates the pinned coordinator and configured worker keys and encrypts payload contents, but does not hide traffic metadata or protect against a malicious authorized worker/coordinator. The median/majority guarantees require fewer than half of verified replicas to be malicious. Real multi-site firewall/DHT acceptance and any actual payout rail must be tested/configured in the target deployment.

## License

MIT.

## Desktop client

`python client.py` starts the optional Tk interface. It requests node keys and a shared AES key for seed/worker operations; `python client.py status job` uses the same CLI as `bitcompute`.
