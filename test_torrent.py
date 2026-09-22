import libtorrent as lt, hashlib, tempfile, time, os

def _session(port):
    s = lt.session({'listen_interfaces': f'0.0.0.0:{port}',
                    'enable_dht': False, 'enable_lsd': False,
                    'enable_upnp': False, 'enable_natpmp': False})
    return s

def make_torrent_info(payload, name):
    d = tempfile.mkdtemp()
    path = f'{d}/{name}'
    with open(path, 'wb') as f: f.write(payload)
    fs = lt.file_storage()
    fs.add_file(name, len(payload))
    t = lt.create_torrent(fs)
    t.piece_length = 16*1024
    lt.set_piece_hashes(t, d)
    return lt.torrent_info(lt.bencode(t.generate()))

def seed_bytes(payload, name, port):
    d = tempfile.mkdtemp()
    path = f'{d}/{name}'
    with open(path, 'wb') as f: f.write(payload)
    info = make_torrent_info(payload, name)
    s = _session(port)
    # use add_torrent_params
    at = lt.add_torrent_params()
    at.save_path = d
    at.info_hashes = lt.sha1_hash(bytes.fromhex(lt.torrent_info(lt.bencode(lt.create_torrent(lt.file_storage()).generate_fingerprint())).to_bytes().hex()))  # fallback - actually reuse info
    # simpler: use h = s.add_torrent({'ti': info, 'save_path': d})
    h = s.add_torrent({'ti': info, 'save_path': d})
    h.set_flags(lt.torrent_flags.seeding_piece_allocation)
    # get hex info_hash
    h_hex = h.info_hash().to_bytes().hex()
    return h, s, h_hex, d

def fetch(info_hash_hex, dest_dir, port, seed_host='127.0.0.1', seed_port=6881):
    s = _session(port)
    at = lt.add_torrent_params()
    at.save_path = dest_dir
    at.info_hashes = lt.sha1_hash(bytes.fromhex(info_hash_hex))
    at.peers.append((seed_host, seed_port, 0))
    return s.add_torrent(at)

def progress(h):
    st = h.status()
    return (st.progress if st.has_metadata else 0.0)

def read_result(h):
    fp = h.torrent_file().files().file_path(0)
    with open(os.path.join(h.status().save_path, fp), 'rb') as f:
        return f.read()

def stop(x):
    if isinstance(x, lt.session): x.pause()
    else: x.pause()

# Test: single roundtrip
payload = b'hello world!'
h, s, ih, d = seed_bytes(payload, 'tiny.bin', 6881)
print('seeded, info_hash:', ih)
h2 = fetch(ih, 'dest', 6901)
t0=time.time()
while time.time()-t0 < 30:
    if progress(h2) >= 1.0: break
    time.sleep(0.3)
print('progress:', progress(h2))
data = read_result(h2)
assert hashlib.sha256(data).digest() == hashlib.sha256(payload).digest(), f'mismatch {hashlib.sha256(data).hexdigest()}'
print('PASS: data', data)
stop(h2); stop(s)