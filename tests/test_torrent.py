import tempfile
import time


def test_torrent_roundtrip_small():
    from bitcompute import torrent as bt
    payload = b"hello world!"
    h, sess, ih = bt.seed_bytes(payload, "tiny.bin", 6881)
    assert len(ih) == 40
    d2 = tempfile.mkdtemp()
    h2, sess2 = bt.fetch(ih, d2, 6882, seed_port=6881)
    assert bt.wait(h2, timeout=30, sess=sess2) is True
    data = b""
    for _ in range(20):
        data = bt.read_result(h2)
        if data == payload:
            break
        time.sleep(0.2)
    assert data == payload
    sess.pause()
    sess2.pause()


def test_torrent_roundtrip_multpiece():
    from bitcompute import torrent as bt
    payload = b"y" * (16 * 1024 * 3 + 7)
    h, sess, ih = bt.seed_bytes(payload, "big.bin", 6883)
    d2 = tempfile.mkdtemp()
    h2, sess2 = bt.fetch(ih, d2, 6884, seed_port=6883)
    assert bt.wait(h2, timeout=30, sess=sess2) is True
    data = b""
    for _ in range(20):
        data = bt.read_result(h2)
        if len(data) == len(payload):
            break
        time.sleep(0.2)
    assert bt.checksum(data) == bt.checksum(payload)
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
    assert bt.wait(h2, timeout=20, sess=sess2) is True
    n = max(bt.peer_count(h2, sess=sess2), bt.peer_count(h, sess=sess))
    assert n >= 1  # farmer/dht nodes visible on at least one side
    sess.pause()
    sess2.pause()
