"""Thin wrapper over libtorrent for seeding/fetching job payloads."""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import libtorrent as lt

PIECE_LENGTH = 16 * 1024
DEFAULT_DHT_ROUTERS = (
    "dht.libtorrent.org:25401,router.bittorrent.com:6881,"
    "router.utorrent.com:6881,dht.transmissionbt.com:6881"
)


def _normalize_bootstrap(value: str) -> str:
    """libtorrent expects a comma-delimited list (accept semicolons for old configs)."""
    return ",".join(part.strip() for part in value.replace(";", ",").split(",") if part.strip())


def _settings(port: int, bootstrap: str = "") -> dict[str, Any]:
    settings: dict[str, Any] = {
        "listen_interfaces": f"0.0.0.0:{port}",
        "enable_dht": True,
        "enable_lsd": False,
        "enable_upnp": False,
        "enable_natpmp": False,
        "enable_incoming_tcp": True,
        "enable_incoming_utp": True,
        "enable_outgoing_utp": True,
        "enable_outgoing_tcp": True,
    }
    if bootstrap:
        settings["dht_bootstrap_nodes"] = _normalize_bootstrap(bootstrap)
    else:
        configured = os.environ.get("BITCOMPUTE_DHT_ROUTERS", "")
        routers = _normalize_bootstrap(configured or DEFAULT_DHT_ROUTERS)
        settings["dht_bootstrap_nodes"] = routers
    return settings


# PEX is enabled by default by libtorrent. Do not set integer bit 2 here: that is
# upload_mode, not a PEX flag. The default flags also retain normal DHT/PEX behavior.
TORRENT_FLAGS = int(lt.torrent_flags.default_flags)
FETCH_FLAGS = int(lt.torrent_flags.default_flags)


def make_torrent_info(payload: bytes, name: str) -> tuple[lt.torrent_info, str]:
    """Build a single-file v1 torrent in a temp dir; return (info, backing_dir)."""
    if not isinstance(payload, bytes):
        raise TypeError("torrent payload must be bytes")
    if not name or os.path.basename(name) != name or name in {".", ".."}:
        raise ValueError("torrent name must be a safe file name")
    directory = tempfile.mkdtemp(prefix="bitcompute-seed-")
    path = os.path.join(directory, name)
    with open(path, "wb") as stream:
        stream.write(payload)

    # libtorrent 2.1 deprecates create_torrent(file_storage); the vector-of-
    # create_file_entry constructor is the supported replacement. Keep a fallback
    # for early 2.0 builds that did not expose create_file_entry in Python.
    if hasattr(lt, "create_file_entry"):
        entry = lt.create_file_entry(name, len(payload))
        creator = lt.create_torrent(
            [entry], PIECE_LENGTH, lt.create_torrent.v1_only
        )
        creator.set_creation_date(0)
    else:  # pragma: no cover - compatibility path for early libtorrent 2.0 wheels
        storage = lt.file_storage()
        storage.add_file(name, len(payload))
        creator = lt.create_torrent(storage)
        creator.piece_length = PIECE_LENGTH
        creator.creation_date = 0

    lt.set_piece_hashes(creator, directory)
    info = lt.torrent_info(lt.bencode(creator.generate()))
    return info, directory


def ih_hex(ihs: lt.info_hash_t) -> str:
    return ihs.get_best().to_bytes().hex()


def ih_from_hex(value: str) -> lt.info_hash_t:
    if not isinstance(value, str) or len(value) != 40:
        raise ValueError("info hash must be exactly 40 hexadecimal characters")
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("info hash must be exactly 40 hexadecimal characters") from exc
    return lt.info_hash_t(lt.sha1_hash(raw))


def hash_of(payload: bytes, name: str) -> str:
    """Deterministic info-hash of a payload (creation date fixed to zero)."""
    info, _ = make_torrent_info(payload, name)
    return ih_hex(info.info_hashes())


def _add_params(info: lt.torrent_info, save_path: str, flags: int) -> lt.add_torrent_params:
    params = lt.add_torrent_params()
    params.ti = info
    params.save_path = save_path
    params.flags = flags
    return params


def seed_bytes(payload: bytes, name: str, port: int, bootstrap: str = ""):
    """Create a single-file torrent and seed it. Returns (handle, session, hex)."""
    info, directory = make_torrent_info(payload, name)
    session = lt.session(_settings(port, bootstrap))
    handle = session.add_torrent(_add_params(info, directory, TORRENT_FLAGS))
    return handle, session, ih_hex(info.info_hashes())


def seed_in_session(session: lt.session, payload: bytes, name: str):
    """Add an extra seeded torrent to an existing session. Returns (handle, hex)."""
    info, directory = make_torrent_info(payload, name)
    handle = session.add_torrent(_add_params(info, directory, TORRENT_FLAGS))
    return handle, ih_hex(info.info_hashes())


def fetch(
    info_hash: lt.info_hash_t | str,
    dest_dir: str,
    port: int,
    seed_host: str = "127.0.0.1",
    seed_port: int = 6881,
    name: str = "manifest.json",
    bootstrap: str = "",
    resume_path: str = "",
    session: lt.session | None = None,
    direct_peer: bool = True,
):
    """Join a swarm. Direct peer injection can be disabled to test/use DHT-only discovery."""
    if not name or os.path.basename(name) != name or name in {".", ".."}:
        raise ValueError("torrent name must be a safe file name")
    os.makedirs(dest_dir, exist_ok=True)
    sess = session if session is not None else lt.session(_settings(port, bootstrap))

    destination = os.path.join(dest_dir, name)
    has_resume = bool(resume_path) and os.path.isfile(resume_path)
    if not has_resume:
        try:
            os.remove(destination)
        except FileNotFoundError:
            pass

    params: lt.add_torrent_params
    if has_resume:
        try:
            params = lt.read_resume_data(Path(resume_path).read_bytes())
        except (OSError, RuntimeError, ValueError):
            # A corrupt/stale resume file must not make a job permanently unusable.
            params = lt.add_torrent_params()
    else:
        params = lt.add_torrent_params()

    params.save_path = dest_dir
    params.flags = FETCH_FLAGS
    params.info_hashes = ih_from_hex(info_hash) if isinstance(info_hash, str) else info_hash
    if direct_peer:
        params.peers = [(seed_host, seed_port)]
    return sess.add_torrent(params), sess


def wait(handle, timeout: float = 30.0, sess=None) -> bool:
    """Poll until metadata + all pieces are in AND the file has been flushed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sess is not None:
            try:
                sess.post_torrent_updates()
                sess.wait_for_alert(50)
            except Exception:  # noqa: BLE001 - shutdown races are harmless here
                pass
        status = handle.status()
        if status.has_metadata and status.progress >= 1.0:
            torrent = handle.torrent_file()
            if torrent is not None:
                path = os.path.join(status.save_path, torrent.name())
                if os.path.isfile(path) and os.path.getsize(path) == torrent.total_size():
                    return True
        time.sleep(0.1)
    return False


def save_resume(handle, resume_path: str, session: lt.session, timeout: float = 10.0) -> None:
    """Save fast-resume data through libtorrent's non-deprecated alert API."""
    handle.save_resume_data()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining_ms = max(1, min(250, int((deadline - time.monotonic()) * 1000)))
        session.wait_for_alert(remaining_ms)
        for alert in session.pop_alerts():
            if isinstance(alert, lt.save_resume_data_alert):
                raw = lt.write_resume_data_buf(alert.params)
                _atomic_write(resume_path, bytes(raw))
                return
            if isinstance(alert, lt.save_resume_data_failed_alert):
                raise RuntimeError(f"libtorrent could not save resume data: {alert.message()}")
    raise TimeoutError(f"libtorrent did not produce resume data within {timeout:g}s")


def _atomic_write(path: str, data: bytes) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".resume-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def read_result(handle) -> bytes:
    """Read the assembled single-file payload bytes."""
    torrent = handle.torrent_file()
    path = os.path.join(handle.status().save_path, torrent.name())
    with open(path, "rb") as stream:
        return stream.read()


def checksum(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _peers_once(handle) -> int:
    status = handle.status()
    return int(getattr(status, "num_peers", 0) or 0) + int(
        getattr(status, "num_seeds", 0) or 0
    )


def peer_count(handle, timeout: float = 12.0, sess=None) -> int:
    """Return currently connected peers (not DHT nodes or historical transfers)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sess is not None:
            try:
                sess.post_torrent_updates()
                sess.wait_for_alert(50)
            except Exception:  # noqa: BLE001 - shutdown races are harmless here
                pass
        peers = _peers_once(handle)
        if peers:
            return peers
        time.sleep(0.2)
    return _peers_once(handle)
