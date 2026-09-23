"""Swarm orchestration: seed a job, work as a compute peer, aggregate results."""
from __future__ import annotations

import json
import os
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from bitcompute import torrent as bt
from bitcompute.capability import Capability
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
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid job in {job_path}: {exc}") from exc


def _announce_result(
    body: dict[str, Any], seed_host: str, announce_port: int, timeout: float
) -> bool:
    """POST a result to the seed control plane, retrying during cold starts."""
    host = seed_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    url = f"http://{host}:{announce_port}/v1/results"
    request = urllib.request.Request(
        url, data=_json_bytes({"result": body}), method="POST",
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
) -> dict[str, Any]:
    """Join a swarm, validate its manifest, compute units and publish results."""
    _validate_port(port, "worker port")
    _validate_port(seed_port, "seed port")
    announce_port = announce_port or _default_announce_port(seed_port)
    _validate_port(announce_port, "announce port")
    if not seed_host or any(c.isspace() for c in seed_host):
        raise ValueError("seed host is invalid")
    if fetch_timeout <= 0 or result_grace < 0 or announce_timeout < 0:
        raise ValueError("timeouts must be positive")

    root = Path(job_dir)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"w{port}"
    dest.mkdir(parents=True, exist_ok=True)
    resume = root / f"resume_{port}.dat"
    bootstrap = f"{seed_host}:{seed_port}"
    handle, sess = bt.fetch(
        magnet, str(dest), port, seed_host=seed_host, seed_port=seed_port,
        bootstrap=bootstrap, resume_path=str(resume),
    )
    try:
        if not bt.wait(handle, timeout=fetch_timeout):
            raise TimeoutError(f"manifest not fetched within {fetch_timeout:g}s (port {port})")
        bt.save_resume(handle, str(resume))
        manifest = JobManifest.from_torrent_payload(bt.read_result(handle))
        shards = dict(zip(manifest.shard_names, manifest.shards))
        executor = get_executor(manifest.executor)
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
            result = executor.run(unit_uid=unit.uid, shard=shard, params=manifest.params)
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
        blob = _json_bytes(body)
        # Register the torrent before publishing the result file. The file is
        # the collector's readiness signal, so this ordering avoids a race in
        # which the seed connects before the result torrent exists.
        bt.seed_in_session(sess, blob, f"result_{port}.json")
        # libtorrent adds torrents asynchronously. Give its network thread a
        # brief chance to advertise the new info-hash before signalling the
        # collector; otherwise an immediate metadata handshake can be missed.
        time.sleep(1.0)
        _atomic_write(root / f"result_{port}.json", blob)
        # The HTTP announcement removes the shared-filesystem requirement.
        # A failed announcement is non-fatal so legacy shared-volume setups
        # and temporarily unavailable coordinators can still recover.
        _announce_result(body, seed_host, announce_port, announce_timeout)
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

    def __init__(self, manifest: JobManifest, worker_ports: tuple[int, ...]):
        self.manifest = manifest
        self.worker_ports = set(worker_ports)
        self._results: dict[int, tuple[dict[str, Any], str]] = {}
        self._lock = threading.Lock()

    def put(self, value: Any, peer_host: str) -> bool:
        if not isinstance(value, dict):
            return False
        port = value.get("worker_port")
        if port not in self.worker_ports or not _valid_result(value, self.manifest, port):
            return False
        with self._lock:
            self._results.setdefault(port, (value, peer_host))
        return True

    def get(self, worker_port: int) -> tuple[dict[str, Any], str] | None:
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
            if length <= 0 or length > MAX_RESULT_BYTES:
                self.send_error(413, "result announcement too large")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                result = payload.get("result") if isinstance(payload, dict) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_error(400, "invalid JSON")
                return
            if not inbox.put(result, self.client_address[0]):
                self.send_error(422, "result does not match this job")
                return
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ResultHandler


def _start_result_server(
    manifest: JobManifest, worker_ports: tuple[int, ...], host: str, port: int
) -> tuple[_ResultInbox, ThreadingHTTPServer, threading.Thread]:
    inbox = _ResultInbox(manifest, worker_ports)
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
    on_result: Callable[[dict[str, Any], str], None] | None = None,
    inbox: _ResultInbox | None = None,
) -> list[dict[str, Any]]:
    """Collect complete result files, ignoring stale, partial and malformed files.

    ``on_result`` runs as soon as a result appears. Production uses this hook
    to pull its torrent while the worker's bounded seeding grace is active.
    """
    deadline = time.monotonic() + timeout
    got: dict[int, dict[str, Any]] = {}
    while time.monotonic() < deadline and len(got) < len(worker_ports):
        for worker_port in worker_ports:
            if worker_port in got:
                continue
            announced = inbox.get(worker_port) if inbox is not None else None
            if announced is not None:
                value, peer_host = announced
            else:
                path = Path(job_dir) / f"result_{worker_port}.json"
                try:
                    if path.stat().st_size > MAX_RESULT_BYTES:
                        continue
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                peer_host = "127.0.0.1"
            if manifest is None or _valid_result(value, manifest, worker_port):
                if on_result is not None:
                    on_result(value, peer_host)
                got[worker_port] = value
        if len(got) < len(worker_ports):
            time.sleep(0.2)
    return list(got.values())


def _verify_result_torrents(
    results: Iterable[dict[str, Any]], timeout: float = 25.0,
    peer_hosts: dict[int, str] | None = None,
) -> dict[int, bool]:
    """Fetch every content-addressed result into an isolated temporary directory."""
    verified: dict[int, bool] = {}
    for result in results:
        worker_port = result["worker_port"]
        blob = _json_bytes(result)
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
                ok = bt.wait(handle, timeout=timeout)
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
) -> dict[str, Any]:
    """Seed a manifest, gather authenticated worker results and aggregate them."""
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

    manifest = load_manifest(job_dir)
    _, session, magnet = bt.seed_bytes(manifest.to_torrent_payload(), "manifest.json", port)
    try:
        inbox, control, control_thread = _start_result_server(
            manifest, worker_ports, announce_host, announce_port
        )
    except BaseException:
        session.pause()
        raise
    try:
        if on_ready is not None:
            on_ready({
                "job_id": manifest.job_id, "magnet": magnet,
                "seed_port": port, "announce_port": control.server_port,
            })
        torrent_ok: dict[int, bool] = {}

        def verify_while_available(result: dict[str, Any], peer_host: str) -> None:
            # Verifying here (not after collection) is essential when workers
            # finish at different times and seed for only a bounded grace.
            worker_port = result["worker_port"]
            torrent_ok.update(_verify_result_torrents(
                [result], timeout=verify_timeout,
                peer_hosts={worker_port: peer_host},
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
        summary.update(
            torrent_verified=torrent_ok, tokens=tokens, magnet=magnet,
            announce_port=control.server_port,
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
