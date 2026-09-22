"""Thin wrapper over libtorrent for seeding/fetching job payloads."""
from __future__ import annotations

import hashlib
import os
import tempfile
import time

import libtorrent as lt


def _session(port: int) -> lt.session:
    return lt.session({
        "listen_interfaces": f"0.0.0.0:{port}",
        "enable_dht": False,
        "enable_lsd": False,
        "enable_upnp": False,
        "enable_natpmp": False,
    })


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
    lt.set_piece_hashes(t, d)
    ti = lt.torrent_info(lt.bencode(t.generate()))
    return ti, d


def ih_hex(ihs: lt.info_hash_t) -> str:
    return ihs.get_best().to_bytes().hex()


def ih_from_hex(h: str) -> lt.info_hash_t:
    return lt.info_hash_t(lt.sha1_hash(bytes.fromhex(h)))


def seed_bytes(payload: bytes, name: str, port: int):
    """Create a single-file torrent and seed it. Returns (handle, session, hex)."""
    ti, d = make_torrent_info(payload, name)
    sess = _session(port)
    h = sess.add_torrent({"ti": ti, "save_path": d})
    return h, sess, ih_hex(ti.info_hashes())


def fetch(info_hash: lt.info_hash_t | str, dest_dir: str, port: int,
          seed_host: str = "127.0.0.1", seed_port: int = 6881,
          name: str = "manifest.json"):
    """Join a swarm via direct peer injection. Returns (handle, session)."""
    sess = _session(port)
    for path in (os.path.join(dest_dir, name),):
        if os.path.isfile(path):
            os.remove(path)
    at = lt.add_torrent_params()
    at.save_path = dest_dir
    at.info_hashes = ih_from_hex(info_hash) if isinstance(info_hash, str) else info_hash
    at.peers = [(seed_host, seed_port)]
    return sess.add_torrent(at), sess


def wait(handle, timeout: float = 30.0) -> bool:
    """Poll until metadata + all pieces are in. Returns True on completion."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = handle.status()
        if st.has_metadata and st.progress >= 1.0:
            return True
        time.sleep(0.3)
    return False


def read_result(handle) -> bytes:
    """Read the assembled single-file payload bytes."""
    tf = handle.torrent_file()
    name = tf.name()
    with open(os.path.join(handle.status().save_path, name), "rb") as f:
        return f.read()


def checksum(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
