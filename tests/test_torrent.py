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


def test_pex_enabled_and_listening():
    from bitcompute import torrent as bt
    h, sess, _ = bt.seed_bytes(b"z" * 100, "m.json", 6885)
    assert not (h.flags() & int(bt.lt.torrent_flags.disable_pex))
    assert sess.is_listening()
    sess.pause()


def test_dht_uses_default_bootstrap_routers():
    from bitcompute import torrent as bt

    assert "," in bt._settings(6886)["dht_bootstrap_nodes"]
    _, sess, _ = bt.seed_bytes(b"q" * 900, "dd.bin", 6886)
    try:
        deadline = time.monotonic() + 5
        while not sess.is_dht_running() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert sess.is_dht_running()
    finally:
        sess.pause()


def test_dht_bootstrap_separator_normalization():
    from bitcompute import torrent as bt

    settings = bt._settings(6886, "192.0.2.10:6881;192.0.2.11:6881")
    assert settings["dht_bootstrap_nodes"] == "192.0.2.10:6881,192.0.2.11:6881"


def test_public_dht_only_discovery():
    """Opt-in external test: find a seed through public DHT, without direct peer injection."""
    import os
    import tempfile

    import pytest

    if os.environ.get("BITCOMPUTE_TEST_PUBLIC_DHT") != "1":
        pytest.skip("set BITCOMPUTE_TEST_PUBLIC_DHT=1 to test external DHT discovery")

    from bitcompute import torrent as bt

    payload = os.urandom(1024 * 1024)
    seed_port, worker_port = 6886, 6887
    h, sess, ih = bt.seed_bytes(payload, "dd.bin", seed_port)
    h2, sess2 = bt.fetch(
        ih, tempfile.mkdtemp(), worker_port, seed_host="127.0.0.1", seed_port=seed_port,
        direct_peer=False,
    )
    try:
        assert bt.peer_count(h2, timeout=45, sess=sess2) >= 1
        assert bt.wait(h2, timeout=90, sess=sess2) is True
        assert bt.read_result(h2) == payload
    finally:
        sess.pause()
        sess2.pause()
