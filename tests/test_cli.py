import json
import os
import threading

import pytest

from bitcompute import cli, node
from bitcompute import torrent as bt


def _write_train_job(jd):
    os.makedirs(jd, exist_ok=True)
    with open(os.path.join(jd, "s1"), "wb") as f:
        f.write("\n".join(f"{x},{2 * x + 1}" for x in range(10)).encode())
    with open(os.path.join(jd, "s2"), "wb") as f:
        f.write("\n".join(f"{x},{2 * x + 1}" for x in range(10, 20)).encode())
    spec = {
        "name": "t", "mode": "train", "shard_files": ["s1", "s2"],
        "units": [{"uid": "r0-s0", "round": 0, "shard": "s1"},
                  {"uid": "r0-s1", "round": 0, "shard": "s2"}],
        "executor": "train_numpy",
        "params": {"lr": 0.01, "steps": 2000}, "redundancy": 1,
    }
    with open(os.path.join(jd, "job.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(spec))


def test_cli_seed_assembles_result(tmp_path, capsys):
    jd = str(tmp_path)
    _write_train_job(jd)
    man = node.load_manifest(jd)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 6971)
    th = [
        threading.Thread(
            target=node.run_worker,
            args=(magnet, jd, 6972, 6971),
            kwargs={"insecure_legacy": True},
        ),
        threading.Thread(
            target=node.run_worker,
            args=(magnet, jd, 6973, 6971),
            kwargs={"insecure_legacy": True},
        ),
    ]
    [t.start() for t in th]
    assert cli.main(["seed", jd, "--port", "6980",
                     "--workers", "6972", "6973", "--insecure-legacy"]) == 0
    sess.pause()
    [t.join(40) for t in th]
    out = json.loads(next(s for s in capsys.readouterr() if s.strip()))
    assert abs(out["w"] - 2.0) < 0.3
    assert os.path.isfile(os.path.join(jd, "result.bin"))


def test_cli_worker_prints_done(tmp_path, capsys):
    jd = str(tmp_path)
    _write_train_job(jd)
    man = node.load_manifest(jd)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 6991)
    assert cli.main(["worker", "--magnet", magnet, "--job-dir", jd,
                     "--port", "6992", "--seed-port", "6991",
                     "--insecure-legacy"]) == 0
    out = json.loads(next(s for s in capsys.readouterr() if s.strip()))
    assert out["worker_port"] == 6992
    assert sorted(out["done"]) == ["r0-s0", "r0-s1"]
    sess.pause()


def test_cli_status_no_summary(capsys):
    import tempfile
    jd = tempfile.mkdtemp()
    assert cli.main(["status", jd]) == 0
    assert json.loads(next(s for s in capsys.readouterr() if s.strip())) == {"state": "in_progress"}


def test_cli_requires_secure_keys_or_explicit_legacy_mode(tmp_path, capsys):
    assert cli.main(["seed", str(tmp_path), "--port", "7001", "--workers", "7002"]) == 1
    assert "secure seed mode needs" in capsys.readouterr().err
    assert cli.main([
        "worker", "--magnet", "0" * 40, "--job-dir", str(tmp_path),
        "--port", "7002", "--seed-port", "7001",
    ]) == 1
    assert "secure worker mode needs" in capsys.readouterr().err


def test_cli_error_output_survives_legacy_windows_encoding(monkeypatch):
    class Cp1252Stream:
        encoding = "cp1252"

        def __init__(self):
            self.value = ""

        def write(self, text):
            text.encode(self.encoding)
            self.value += text

    stream = Cp1252Stream()
    monkeypatch.setattr(cli.sys, "stderr", stream)
    cli._print_error(ValueError("bad replacement character: \ufffd"))
    assert stream.value == "bitcompute: error: bad replacement character: \\ufffd\n"


def test_python_node_api_requires_explicit_legacy_opt_in(tmp_path):
    with pytest.raises(ValueError, match="insecure_legacy=True"):
        node.seed_job(str(tmp_path / "seed"), port=7101, worker_ports=(7102,))
    with pytest.raises(ValueError, match="insecure_legacy=True"):
        node.run_worker("0" * 40, str(tmp_path / "worker"), 7102, 7101)


def test_cli_keygen_writes_node_and_encryption_keys(tmp_path, capsys):
    private = tmp_path / "node.key"
    secret = tmp_path / "job.key"
    assert cli.main([
        "keygen", "--identity", str(private), "--encryption-key", str(secret),
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert private.is_file() and (tmp_path / "node.key.pub").is_file()
    assert secret.stat().st_size == 32
    assert len(output["identity_fingerprint"]) == 64

