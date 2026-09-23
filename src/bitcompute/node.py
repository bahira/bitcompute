"""Swarm orchestration: seed a job, work as a compute peer, aggregate results."""
from __future__ import annotations

import base64
import json
import os
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from bitcompute import sandbox, security, settlement
from bitcompute import torrent as bt
from bitcompute.capability import Capability
from bitcompute.executor import custom_executor_target, is_builtin
from bitcompute.executor import get as get_executor
from bitcompute.incentive import Ledger, should_unchoke
from bitcompute.manifest import SCHEMA_VERSION, JobManifest, WorkUnit
from bitcompute.verify import majority_vote, median_vote

MAX_RESULT_BYTES = 64 * 1024 * 1024


def _fmt_for(executor: str) -> str | None:
    return {"train_numpy": "2d", "mock": "2d"}.get(executor)


def _validate_port(port: int, label: str = "port") -> int:
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError(f"{label} must be between 1 and 65535")
    return port


def _default_announce_port(seed_port: int) -> int:
    """Derive a stable control-plane port without colliding with BitTorrent."""
    return seed_port + 1000 if seed_port <= 64535 else seed_port - 1000


def _format_host_port(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"


def _load_shared_key(value: str | Path | bytes | None) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        if len(value) != security.KEY_BYTES:
            raise ValueError("encryption key must be 32 bytes")
        return value
    return security.load_encryption_key(value)


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _atomic_write(path: str | Path, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_manifest(job_dir: str) -> JobManifest:
    """Load and validate a local job without allowing shard path traversal."""
    root = Path(job_dir).resolve()
    job_path = root / "job.json"
    try:
        with job_path.open(encoding="utf-8") as stream:
            spec = json.load(stream)
        if not isinstance(spec, dict):
            raise ValueError("job.json root must be an object")
        names = spec.get("shard_files", [])
        if not isinstance(names, list):
            raise ValueError("shard_files must be an array")
        shards: list[bytes] = []
        for name in names:
            if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
                raise ValueError(f"unsafe shard file name: {name!r}")
            shards.append((root / name).read_bytes())
        units = [WorkUnit(**unit) for unit in spec["units"]]
        return JobManifest.create(
            name=spec["name"], mode=spec["mode"], shards=shards, units=units,
            executor=spec["executor"], params=spec.get("params", {}),
            redundancy=spec.get("redundancy", 1), shard_names=names,
            schema=spec.get("schema", SCHEMA_VERSION),
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid job in {job_path}: {exc}") from exc


def _announce_result(
    body: dict[str, Any], seed_host: str, announce_port: int, timeout: float,
    envelope: bytes | None = None,
) -> bool:
    """POST a result to the seed control plane, retrying during cold starts."""
    host = seed_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    url = f"http://{host}:{announce_port}/v1/results"
    payload = (
        {"envelope": base64.b64encode(envelope).decode("ascii")}
        if envelope is not None else {"result": body}
    )
    request = urllib.request.Request(
        url, data=_json_bytes(payload), method="POST",
        headers={"Content-Type": "application/json"},
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(request, timeout=min(2.0, max(0.1, timeout))) as response:
                return response.status == 202
        except (OSError, urllib.error.URLError):
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def run_worker(
    magnet: str,
    job_dir: str,
    port: int,
    seed_port: int,
    seed_host: str = "127.0.0.1",
    *,
    announce_port: int | None = None,
    announce_timeout: float = 3.0,
    fetch_timeout: float = 120.0,
    result_grace: float = 20.0,
    identity_key: str | Path | None = None,
    trusted_seed_key: str | Path | None = None,
    encryption_key: str | Path | bytes | None = None,
    allow_custom_executor: bool = False,
    insecure_legacy: bool = False,
) -> dict[str, Any]:
    """Join a swarm; unsigned, unencrypted operation needs an explicit opt-in."""
    _validate_port(port, "worker port")
    _validate_port(seed_port, "seed port")
    announce_port = announce_port or _default_announce_port(seed_port)
    _validate_port(announce_port, "announce port")
    if not seed_host or any(c.isspace() for c in seed_host):
        raise ValueError("seed host is invalid")
    if fetch_timeout <= 0 or result_grace < 0 or announce_timeout < 0:
        raise ValueError("timeouts must be positive")
    if not isinstance(insecure_legacy, bool):
        raise TypeError("insecure_legacy must be a bool")
    secure_values = (identity_key, trusted_seed_key, encryption_key)
    secure = any(value is not None for value in secure_values)
    if secure and not all(value is not None for value in secure_values):
        raise ValueError(
            "secure worker mode requires identity_key, trusted_seed_key, and encryption_key"
        )
    if secure and insecure_legacy:
        raise ValueError("do not combine insecure_legacy with secure worker keys")
    if not secure and not insecure_legacy:
        raise ValueError("secure worker keys are required unless insecure_legacy=True")
    worker_identity = security.load_private_key(identity_key) if secure else None
    seed_identity = security.load_public_key(trusted_seed_key) if secure else None
    shared_key = _load_shared_key(encryption_key) if secure else None

    root = Path(job_dir)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"w{port}"
    dest.mkdir(parents=True, exist_ok=True)
    resume = root / f"resume_{port}.dat"
    bootstrap = _format_host_port(seed_host.strip("[]"), seed_port)
    handle, sess = bt.fetch(
        magnet, str(dest), port, seed_host=seed_host, seed_port=seed_port,
        bootstrap=bootstrap, resume_path=str(resume),
    )
    try:
        if not bt.wait(handle, timeout=fetch_timeout, sess=sess):
            # A resume cache can outlive its downloaded file. Force a recheck
            # once so libtorrent rewrites it from the available pieces.
            try:
                handle.force_recheck()
            except Exception:  # noqa: BLE001 - optional libtorrent operation
                pass
            time.sleep(0.5)
            if not bt.wait(handle, timeout=fetch_timeout, sess=sess):
                raise TimeoutError(
                    f"manifest not fetched within {fetch_timeout:g}s (port {port})"
                )
        bt.save_resume(handle, str(resume), sess)
        manifest_payload = bt.read_result(handle)
        if secure:
            manifest_payload, _, _ = security.verify_envelope(
                manifest_payload,
                purpose="manifest",
                trusted_public_key=seed_identity,
                encryption_key=shared_key,
            )
        manifest = JobManifest.from_torrent_payload(manifest_payload)
        shards = dict(zip(manifest.shard_names, manifest.shards))
        plugin_target: tuple[str, str] | None = None
        if is_builtin(manifest.executor):
            executor = get_executor(manifest.executor)
        else:
            if not allow_custom_executor:
                raise PermissionError(
                    f"custom executor {manifest.executor!r} is disabled; "
                    "pass allow_custom_executor=True to opt in"
                )
            plugin_target = custom_executor_target(manifest.executor)
            if plugin_target is None:
                raise KeyError(f"unknown custom executor {manifest.executor!r}")
            executor = None
        cap = Capability(job_id=manifest.job_id, executors=[manifest.executor])
        out: dict[str, str] = {}
        for unit in manifest.units:
            if not cap.wants(unit.uid):
                continue
            key = unit.shard if manifest.mode == "train" else unit.input_ref
            if manifest.mode == "infer" and key not in shards:
                shard = (key or "").encode("utf-8")
            else:
                shard = shards.get(key or "", b"")
            if plugin_target is None:
                assert executor is not None
                result = executor.run(unit_uid=unit.uid, shard=shard, params=manifest.params)
            else:
                result = sandbox.run_custom_executor(
                    *plugin_target, unit_uid=unit.uid, shard=shard, params=manifest.params
                )
            if not isinstance(result, bytes):
                raise TypeError(f"executor {manifest.executor!r} returned a non-bytes result")
            out[unit.uid] = result.hex()
            cap.have.add(unit.uid)
        body: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "job_id": manifest.job_id,
            "worker_port": port,
            "executor": manifest.executor,
            "units": out,
        }
        if secure and worker_identity is not None:
            body["worker_id"] = security.key_fingerprint(worker_identity.public_key())
        blob = _json_bytes(body)
        wire_payload = (
            security.sign_envelope(
                blob, worker_identity, purpose=f"result:{manifest.job_id}",
                encryption_key=shared_key,
            )
            if secure else blob
        )
        # Register the torrent before publishing the result file. The file is
        # the collector's readiness signal, so this ordering avoids a race in
        # which the seed connects before the result torrent exists.
        bt.seed_in_session(sess, wire_payload, f"result_{port}.json")
        # libtorrent adds torrents asynchronously. Give its network thread a
        # brief chance to advertise the new info-hash before signalling the
        # collector; otherwise an immediate metadata handshake can be missed.
        time.sleep(1.0)
        _atomic_write(root / f"result_{port}.json", wire_payload)
        # The HTTP announcement removes the shared-filesystem requirement.
        # A failed announcement is non-fatal so legacy shared-volume setups
        # and temporarily unavailable coordinators can still recover.
        _announce_result(
            body, seed_host, announce_port, announce_timeout,
            envelope=wire_payload if secure else None,
        )
        if result_grace:
            time.sleep(result_grace)
        return body
    finally:
        sess.pause()


def _valid_result(value: Any, manifest: JobManifest, expected_port: int) -> bool:
    if not isinstance(value, dict):
        return False
    if (value.get("schema_version") != SCHEMA_VERSION
            or value.get("job_id") != manifest.job_id
            or value.get("worker_port") != expected_port
            or value.get("executor") != manifest.executor):
        return False
    units = value.get("units")
    expected = {unit.uid for unit in manifest.units}
    if not isinstance(units, dict) or set(units) != expected:
        return False
    try:
        for encoded in units.values():
            if not isinstance(encoded, str) or len(encoded) > MAX_RESULT_BYTES * 2:
                return False
            bytes.fromhex(encoded)
    except ValueError:
        return False
    return True


class _ResultInbox:
    """Thread-safe, job-scoped result announcement inbox."""

    def __init__(
        self,
        manifest: JobManifest,
        worker_ports: tuple[int, ...],
        worker_public_keys: dict[int, Any] | None = None,
        encryption_key: bytes | None = None,
    ):
        self.manifest = manifest
        self.worker_ports = set(worker_ports)
        self.worker_public_keys = worker_public_keys
        self.encryption_key = encryption_key
        self.secure = worker_public_keys is not None
        self._results: dict[int, tuple[dict[str, Any], str, bytes | None]] = {}
        self._lock = threading.Lock()

    def decode_envelope(self, envelope: bytes) -> tuple[dict[str, Any], int] | None:
        if not self.secure or self.worker_public_keys is None:
            return None
        try:
            payload, public_key, fingerprint = security.verify_envelope(
                envelope,
                purpose=f"result:{self.manifest.job_id}",
                encryption_key=self.encryption_key,
            )
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        port = value.get("worker_port")
        if (not isinstance(port, int) or isinstance(port, bool)
                or port not in self.worker_ports):
            return None
        expected_key = self.worker_public_keys.get(port)
        if expected_key is None:
            return None
        if security.public_key_bytes(public_key) != security.public_key_bytes(expected_key):
            return None
        if value.get("worker_id") != fingerprint:
            return None
        if not _valid_result(value, self.manifest, port):
            return None
        return value, port

    def put_envelope(self, envelope: bytes, peer_host: str) -> bool:
        decoded = self.decode_envelope(envelope)
        if decoded is None:
            return False
        value, port = decoded
        with self._lock:
            self._results.setdefault(port, (value, peer_host, envelope))
        return True

    def put(self, value: Any, peer_host: str) -> bool:
        if self.secure or not isinstance(value, dict):
            return False
        port = value.get("worker_port")
        if port not in self.worker_ports or not _valid_result(value, self.manifest, port):
            return False
        with self._lock:
            self._results.setdefault(port, (value, peer_host, None))
        return True

    def get(self, worker_port: int) -> tuple[dict[str, Any], str, bytes | None] | None:
        with self._lock:
            return self._results.get(worker_port)


def _handler_for(inbox: _ResultInbox):
    class ResultHandler(BaseHTTPRequestHandler):
        server_version = "bitcompute-control/1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(5.0)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != "/healthz":
                self.send_error(404)
                return
            body = _json_bytes({"status": "ok", "job_id": inbox.manifest.job_id})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != "/v1/results":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "invalid content length")
                return
            if length <= 0 or length > MAX_RESULT_BYTES * 2:
                self.send_error(413, "result announcement too large")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_error(400, "invalid JSON")
                return
            if not isinstance(payload, dict):
                self.send_error(400, "request body must be an object")
                return
            if inbox.secure:
                encoded = payload.get("envelope")
                try:
                    envelope = base64.b64decode(encoded, validate=True)
                except (ValueError, base64.binascii.Error, TypeError):
                    self.send_error(400, "invalid signed result envelope")
                    return
                accepted = inbox.put_envelope(envelope, self.client_address[0])
            else:
                accepted = inbox.put(payload.get("result"), self.client_address[0])
            if not accepted:
                self.send_error(422, "result does not match this job or trusted worker key")
                return
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ResultHandler


def _start_result_server(
    manifest: JobManifest,
    worker_ports: tuple[int, ...],
    host: str,
    port: int,
    worker_public_keys: dict[int, Any] | None = None,
    encryption_key: bytes | None = None,
) -> tuple[_ResultInbox, ThreadingHTTPServer, threading.Thread]:
    inbox = _ResultInbox(manifest, worker_ports, worker_public_keys, encryption_key)
    server = ThreadingHTTPServer((host, port), _handler_for(inbox))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="result-control", daemon=True)
    thread.start()
    return inbox, server, thread


def _collect(
    job_dir: str,
    worker_ports: tuple[int, ...],
    manifest: JobManifest | None = None,
    timeout: float = 75.0,
    on_result: Callable[[dict[str, Any], str, bytes | None], None] | None = None,
    inbox: _ResultInbox | None = None,
) -> list[dict[str, Any]]:
    """Collect complete result files, ignoring stale, partial and malformed files.

    ``on_result`` runs as soon as a result appears. The coordinator pulls each
    torrent while the worker's bounded seeding grace is active.
    """
    deadline = time.monotonic() + timeout
    got: dict[int, dict[str, Any]] = {}
    while time.monotonic() < deadline and len(got) < len(worker_ports):
        for worker_port in worker_ports:
            if worker_port in got:
                continue
            announced = inbox.get(worker_port) if inbox is not None else None
            envelope: bytes | None = None
            if announced is not None:
                value, peer_host, envelope = announced
            else:
                path = Path(job_dir) / f"result_{worker_port}.json"
                try:
                    if path.stat().st_size > MAX_RESULT_BYTES * 2:
                        continue
                    if inbox is not None and inbox.secure:
                        envelope = path.read_bytes()
                        decoded = inbox.decode_envelope(envelope)
                        if decoded is None or decoded[1] != worker_port:
                            continue
                        value = decoded[0]
                    else:
                        value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                peer_host = "127.0.0.1"
            if manifest is None or _valid_result(value, manifest, worker_port):
                if on_result is not None:
                    on_result(value, peer_host, envelope)
                got[worker_port] = value
        if len(got) < len(worker_ports):
            time.sleep(0.2)
    return list(got.values())


def _verify_result_torrents(
    results: Iterable[dict[str, Any]],
    timeout: float = 25.0,
    peer_hosts: dict[int, str] | None = None,
    wire_payloads: dict[int, bytes] | None = None,
) -> dict[int, bool]:
    """Fetch every content-addressed result into an isolated temporary directory."""
    verified: dict[int, bool] = {}
    for result in results:
        worker_port = result["worker_port"]
        blob = (wire_payloads or {}).get(worker_port, _json_bytes(result))
        info_hash = bt.hash_of(blob, f"result_{worker_port}.json")
        # Port 0 asks the OS for an available ephemeral listen port and avoids
        # collisions with worker ports on dense single-host deployments.
        session = bt.lt.session(bt._settings(0))
        try:
            with tempfile.TemporaryDirectory(prefix="bitcompute-verify-") as dest:
                handle, _ = bt.fetch(
                    info_hash, dest, worker_port,
                    seed_host=(peer_hosts or {}).get(worker_port, "127.0.0.1"),
                    seed_port=worker_port, name=f"result_{worker_port}.json",
                    session=session,
                )
                ok = bt.wait(handle, timeout=timeout, sess=session)
                downloaded = bt.read_result(handle) if ok else b""
                verified[worker_port] = ok and downloaded == blob
        finally:
            session.pause()
    return verified


def seed_job(
    job_dir: str,
    port: int = 6881,
    worker_ports: tuple[int, ...] = (6882, 6883),
    *,
    announce_host: str = "0.0.0.0",
    announce_port: int | None = None,
    collect_timeout: float = 75.0,
    verify_timeout: float = 25.0,
    on_ready: Callable[[dict[str, Any]], None] | None = None,
    identity_key: str | Path | None = None,
    worker_public_keys: Mapping[int | str, str | Path] | None = None,
    encryption_key: str | Path | bytes | None = None,
    settlement_path: str | Path | None = None,
    credits_per_unit: int = 1,
    insecure_legacy: bool = False,
) -> dict[str, Any]:
    """Seed a manifest; unsigned, unencrypted operation needs an explicit opt-in."""
    _validate_port(port, "seed port")
    worker_ports = tuple(worker_ports)
    if not worker_ports or len(set(worker_ports)) != len(worker_ports):
        raise ValueError("worker ports must be a non-empty unique list")
    for worker_port in worker_ports:
        _validate_port(worker_port, "worker port")
    if port in worker_ports:
        raise ValueError("seed port must differ from worker ports")
    announce_port = announce_port or _default_announce_port(port)
    _validate_port(announce_port, "announce port")
    if not announce_host or any(c.isspace() for c in announce_host):
        raise ValueError("announce host is invalid")
    if collect_timeout <= 0 or verify_timeout <= 0:
        raise ValueError("timeouts must be positive")
    if (not isinstance(credits_per_unit, int) or isinstance(credits_per_unit, bool)
            or credits_per_unit <= 0):
        raise ValueError("credits_per_unit must be a positive integer")

    if not isinstance(insecure_legacy, bool):
        raise TypeError("insecure_legacy must be a bool")
    secure_values = (identity_key, worker_public_keys, encryption_key)
    secure = any(value is not None for value in secure_values)
    if secure and not all(value is not None for value in secure_values):
        raise ValueError(
            "secure seed mode requires identity_key, worker_public_keys, and encryption_key"
        )
    if secure and insecure_legacy:
        raise ValueError("do not combine insecure_legacy with secure seed keys")
    if not secure and not insecure_legacy:
        raise ValueError("secure seed keys are required unless insecure_legacy=True")
    seed_signing_key = security.load_private_key(identity_key) if secure else None
    shared_key = _load_shared_key(encryption_key) if secure else None
    trusted_workers: dict[int, Any] | None = None
    if secure:
        assert worker_public_keys is not None
        trusted_workers = {}
        for worker, key_path in worker_public_keys.items():
            worker_id = int(worker)
            _validate_port(worker_id, "worker key port")
            if worker_id in trusted_workers:
                raise ValueError(f"duplicate worker key for port {worker_id}")
            trusted_workers[worker_id] = security.load_public_key(key_path)
        if set(trusted_workers) != set(worker_ports):
            raise ValueError("secure seed mode needs exactly one pinned public key per worker port")

    manifest = load_manifest(job_dir)
    manifest_payload = manifest.to_torrent_payload()
    if secure:
        manifest_payload = security.sign_envelope(
            manifest_payload, seed_signing_key, purpose="manifest", encryption_key=shared_key
        )
    _, session, magnet = bt.seed_bytes(manifest_payload, "manifest.json", port)
    try:
        inbox, control, control_thread = _start_result_server(
            manifest, worker_ports, announce_host, announce_port,
            worker_public_keys=trusted_workers, encryption_key=shared_key,
        )
    except BaseException:
        session.pause()
        raise
    try:
        if on_ready is not None:
            event = {
                "job_id": manifest.job_id, "magnet": magnet,
                "seed_port": port, "announce_port": control.server_port,
            }
            if secure and seed_signing_key is not None:
                event["seed_fingerprint"] = security.key_fingerprint(
                    seed_signing_key.public_key()
                )
            on_ready(event)
        torrent_ok: dict[int, bool] = {}

        def verify_while_available(
            result: dict[str, Any], peer_host: str, envelope: bytes | None
        ) -> None:
            # Verify while a remote worker is still seeding its signed payload.
            worker_port = result["worker_port"]
            torrent_ok.update(_verify_result_torrents(
                [result], timeout=verify_timeout,
                peer_hosts={worker_port: peer_host},
                wire_payloads={worker_port: envelope} if envelope is not None else None,
            ))

        results = _collect(
            job_dir, worker_ports, manifest, timeout=collect_timeout,
            on_result=verify_while_available, inbox=inbox,
        )
        if len(results) < manifest.redundancy:
            raise RuntimeError(
                f"received {len(results)} valid results, but redundancy requires "
                f"at least {manifest.redundancy}"
            )
        results = [result for result in results if torrent_ok.get(result["worker_port"])]
        if len(results) < manifest.redundancy:
            raise RuntimeError("not enough results passed torrent verification")

        ledger = Ledger()
        for result in results:
            size = len(_json_bytes(result))
            ledger.record(str(result["worker_port"]), contributed=size, received=1)
        ranked = should_unchoke(ledger, max_unchoked=max(1, len(results)))
        results.sort(key=lambda result: (
            ranked.index(str(result["worker_port"]))
            if str(result["worker_port"]) in ranked else len(ranked),
            result["worker_port"],
        ))
        tokens = {
            str(result["worker_port"]): {
                "bytes": len(_json_bytes(result)), "pieces": len(result["units"]),
            }
            for result in results
        }

        if manifest.mode == "train":
            packed = [
                bytes.fromhex(encoded)
                for result in results for encoded in result["units"].values()
            ]
            fmt = _fmt_for(manifest.executor) or "2d"
            final = median_vote(packed, fmt)
            values = struct.unpack("<" + fmt, final)
            _atomic_write(Path(job_dir) / "result.bin", final)
            summary: dict[str, Any] = {
                "job_id": manifest.job_id, "mode": "train",
                "workers": len(results), "values": list(values),
            }
            # Backwards-compatible names for the built-in two-parameter trainer.
            if len(values) == 2:
                summary.update(w=values[0], b=values[1])
        else:
            merged: dict[str, str] = {}
            for unit in manifest.units:
                values = [bytes.fromhex(result["units"][unit.uid]) for result in results]
                merged[unit.uid] = majority_vote(values).decode("utf-8", errors="replace")
            _atomic_write(Path(job_dir) / "result.json", _json_bytes(merged))
            summary = {
                "job_id": manifest.job_id, "mode": "infer",
                "units": merged, "workers": len(results),
            }
        ledger_path = Path(settlement_path) if settlement_path is not None else Path(job_dir) / "settlement.sqlite3"
        receipts = settlement.CreditSettlementLedger(ledger_path).settle_job(
            manifest.job_id, results, credits_per_unit=credits_per_unit,
            coordinator_key=seed_signing_key,
        )
        summary.update(
            torrent_verified=torrent_ok, tokens=tokens, magnet=magnet,
            announce_port=control.server_port,
            settlement={
                "currency": settlement.CURRENCY,
                "state": "credit-recorded",
                "credits_per_unit": credits_per_unit,
                "ledger": str(ledger_path),
                "receipts": receipts,
            },
        )
        _atomic_write(Path(job_dir) / "summary.json", _json_bytes(summary))
        return summary
    finally:
        control.shutdown()
        control.server_close()
        control_thread.join(timeout=2.0)
        session.pause()


def status(job_dir: str) -> dict[str, Any]:
    path = Path(job_dir) / "summary.json"
    if not path.is_file():
        return {"state": "in_progress"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"state": "error", "error": f"invalid summary: {exc}"}
    return value if isinstance(value, dict) else {"state": "error", "error": "invalid summary"}
