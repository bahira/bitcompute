import json
import os

from bitcompute import cli
from bitcompute import node
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
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 6921)
    node.run_worker(magnet, jd, 6922, 6921)
    node.run_worker(magnet, jd, 6923, 6921)
    sess.pause()
    assert cli.main(["seed", jd, "--port", "6924",
                     "--workers", "6922", "6923"]) == 0
    out = json.loads((next(s for s in capsys.readouterr() if s.strip())))
    assert abs(out["w"] - 2.0) < 0.3
    assert os.path.isfile(os.path.join(jd, "result.bin"))


def test_cli_worker_prints_done(tmp_path, capsys):
    jd = str(tmp_path)
    _write_train_job(jd)
    man = node.load_manifest(jd)
    _, sess, magnet = bt.seed_bytes(man.to_torrent_payload(), "manifest.json", 6931)
    assert cli.main(["worker", "--magnet", magnet, "--job-dir", jd,
                     "--port", "6932", "--seed-port", "6931"]) == 0
    out = json.loads((next(s for s in capsys.readouterr() if s.strip())))
    assert out["worker_port"] == 6932
    assert sorted(out["done"]) == ["r0-s0", "r0-s1"]
    sess.pause()


def test_cli_status_no_summary(capsys):
    import tempfile
    jd = tempfile.mkdtemp()
    assert cli.main(["status", jd]) == 0
    assert json.loads((next(s for s in capsys.readouterr() if s.strip()))) == {"state": "in_progress"}
