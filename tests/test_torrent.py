import tempfile


def test_torrent_roundtrip_small():
    from bitcompute import torrent as bt
    payload = b"hello world!"
    h, sess, ih = bt.seed_bytes(payload, "tiny.bin", 6881)
    assert len(ih) == 40
    d2 = tempfile.mkdtemp()
    h2, sess2 = bt.fetch(ih, d2, 6882, seed_port=6881)
    assert bt.wait(h2, timeout=30) is True
    data = bt.read_result(h2)
    assert data == payload
    sess.pause()
    sess2.pause()


def test_torrent_roundtrip_multpiece():
    from bitcompute import torrent as bt
    payload = b"y" * (16 * 1024 * 3 + 7)
    h, sess, ih = bt.seed_bytes(payload, "big.bin", 6883)
    d2 = tempfile.mkdtemp()
    h2, sess2 = bt.fetch(ih, d2, 6884, seed_port=6883)
    assert bt.wait(h2, timeout=30) is True
    assert bt.checksum(bt.read_result(h2)) == bt.checksum(payload)
    sess.pause()
    sess2.pause()


def test_pex_flag_and_listening():
    from bitcompute import torrent as bt
    h, sess, _ = bt.seed_bytes(b"z" * 100, "m.json", 6885)
    assert h.status().flags & 2  # pex bit set
    assert sess.is_listening()
    sess.pause()


def test_dht_auto_discovery():
    from bitcompute import torrent as bt
    import tempfile
    h, sess, ih = bt.seed_bytes(b"q" * 900, "dd.json", 6886)
    h2, sess2 = bt.fetch(ih, tempfile.mkdtemp(), 6887, seed_port=6886)
    assert bt.wait(h2, timeout=20) is True
    assert bt.peer_count(h) >= 1 and bt.peer_count(h2) >= 1
    sess.pause()
    sess2.pause()
