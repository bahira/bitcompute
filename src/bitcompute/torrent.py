"""Thin wrapper over libtorrent for seeding/fetching job payloads."""
from __future__ import annotations

import hashlib
import os
import tempfile
import time

import libtorrent as lt


def _settings(port: int, bootstrap: str = "") -> dict:
    s = {
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
        s["dht_bootstrap_nodes"] = bootstrap
    else:
        routers = [r for r in os.environ.get("BITCOMPUTE_DHT_ROUTERS", "").split(";") if r]
        if routers:
            s["dht_bootstrap_nodes"] = ";".join(routers)
    return s


TORRENT_FLAGS = int(lt.torrent_flags.default_flags) | 2  # seed side: +pex bit
FETCH_FLAGS = int(lt.torrent_flags.default_flags)  # fetch side: pex off (direct peers win, 2.x quirk)


def make_torrent_info(payload: bytes, name: str) -> tuple[lt.torrent_info, str]:
    """Build a single-file torrent in a temp dir. Returns (torrent_info, dir)."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, name)
    with open(path, "wb") as f:
        f.write(payload)
    fs = lt.file_storage()
    fs.add_file(name, len(payload))
    t = lt.create_torrent(fs)
    t.piece_length = 16 * 1024
    t.creation_date = 0
    lt.set_piece_hashes(t, d)
    ti = lt.torrent_info(lt.bencode(t.generate()))
    return ti, d


def ih_hex(ihs: lt.info_hash_t) -> str:
    return ihs.get_best().to_bytes().hex()


def ih_from_hex(h: str) -> lt.info_hash_t:
    return lt.info_hash_t(lt.sha1_hash(bytes.fromhex(h)))


def hash_of(payload: bytes, name: str) -> str:
    """Deterministic info-hash of a payload (creation_date fixed to 0)."""
    ti, _ = make_torrent_info(payload, name)
    return ih_hex(ti.info_hashes())


def seed_bytes(payload: bytes, name: str, port: int, bootstrap: str = ""):
    """Create a single-file torrent and seed it. Returns (handle, session, hex)."""
    ti, d = make_torrent_info(payload, name)
    sess = lt.session(_settings(port, bootstrap))
    h = sess.add_torrent({"ti": ti, "save_path": d, "flags": TORRENT_FLAGS})
    return h, sess, ih_hex(ti.info_hashes())


def seed_in_session(sess: lt.session, payload: bytes, name: str):
    """Add an extra seeded torrent to an existing session. Returns (handle, hex)."""
    ti, d = make_torrent_info(payload, name)
    h = sess.add_torrent({"ti": ti, "save_path": d, "flags": TORRENT_FLAGS})
    return h, ih_hex(ti.info_hashes())


def fetch(info_hash: lt.info_hash_t | str, dest_dir: str, port: int,
           seed_host: str = "127.0.0.1", seed_port: int = 6881,
           name: str = "manifest.json", bootstrap: str = "",
           resume_path: str = "", session: lt.session | None = None):
    """Join a swarm via direct peer injection (+DHT bootstrap). Returns (handle, session)."""
    sess = session if session is not None else lt.session(_settings(port, bootstrap))
    for path in (os.path.join(dest_dir, name),):
        try:
            os.remove(path)
        except OSError:
            pass
    at = lt.add_torrent_params()
    at.flags = FETCH_FLAGS
    at.save_path = dest_dir
    at.info_hashes = ih_from_hex(info_hash) if isinstance(info_hash, str) else info_hash
    at.peers = [(seed_host, seed_port)]
    if resume_path and os.path.isfile(resume_path):
        with open(resume_path, "rb") as f:
            raw = f.read()
        if raw:
            at.resume_data = list(raw.decode("latin-1"))
    return sess.add_torrent(at), sess if session is None else sess


def wait(handle, timeout: float = 30.0) -> bool:
    """Poll until metadata + all pieces are in AND the file is fully flushed."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = handle.status()
        if st.has_metadata and st.progress >= 1.0:
            tf = handle.torrent_file()
            if tf is not None:
                p = os.path.join(st.save_path, tf.name())
                if os.path.isfile(p) and os.path.getsize(p) == tf.total_size():
                    return True
        time.sleep(0.1)
    return False


def save_resume(handle, resume_path: str) -> None:
    raw = lt.bencode(handle.write_resume_data())
    with open(resume_path, "wb") as f:
        f.write(raw)


def read_result(handle) -> bytes:
    """Read the assembled single-file payload bytes."""
    tf = handle.torrent_file()
    name = tf.name()
    with open(os.path.join(handle.status().save_path, name), "rb") as f:
        return f.read()


def checksum(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _peers_once(handle) -> int:
    st = handle.status()
    try:
        d = st.dict()
        v = d.get("peers") or d.get("num_peers") or d.get("num_seeds") or 0
        return int(v)
    except (AttributeError, TypeError):
        return int(getattr(st, "num_peers", 0) or 0) + \
            int(getattr(st, "num_seeds", 0) or 0)


def peer_count(handle, timeout: float = 12.0) -> int:
    """Connected peers/farmers seen by a torrent handle (polls until >=1)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        n = _peers_once(handle)
        if n >= 1:
            return n
        time.sleep(0.2)
    return _peers_once(handle)
