import json

import pytest

from bitcompute import node
from bitcompute.manifest import JobManifest, WorkUnit


def _manifest():
    return JobManifest.create(
        name="safe",
        mode="train",
        shards=[b"0,1\n"],
        shard_names=["data.csv"],
        units=[WorkUnit(uid="u1", round=0, shard="data.csv")],
        executor="train_numpy",
        params={},
    )


def test_swarm_manifest_rejects_tampered_content():
    manifest = _manifest()
    payload = json.loads(manifest.to_torrent_payload())
    payload["params"]["steps"] = 999
    with pytest.raises(ValueError, match="job_id"):
        JobManifest.from_torrent_payload(json.dumps(payload).encode())


def test_manifest_rejects_duplicate_units_and_unsafe_shards():
    with pytest.raises(ValueError, match="duplicate"):
        JobManifest.create(
            name="bad", mode="train", shards=[b"x"], shard_names=["x"],
            units=[WorkUnit("same", 0, shard="x"), WorkUnit("same", 1, shard="x")],
            executor="train_numpy", params={},
        )
    with pytest.raises(ValueError, match="unsafe"):
        JobManifest.create(
            name="bad", mode="train", shards=[b"x"], shard_names=["../x"],
            units=[WorkUnit("u", 0, shard="../x")], executor="train_numpy", params={},
        )


def test_load_manifest_blocks_path_traversal(tmp_path):
    (tmp_path / "job.json").write_text(json.dumps({
        "name": "bad", "mode": "train", "shard_files": ["../secret"],
        "units": [{"uid": "u", "round": 0, "shard": "../secret"}],
        "executor": "train_numpy", "params": {},
    }))
    with pytest.raises(ValueError, match="unsafe shard"):
        node.load_manifest(str(tmp_path))


def test_stale_or_wrong_worker_result_is_rejected():
    manifest = _manifest()
    result = {
        "schema_version": 1, "job_id": "old-job", "worker_port": 6882,
        "executor": "train_numpy", "units": {"u1": "00"},
    }
    assert not node._valid_result(result, manifest, 6882)
    result["job_id"] = manifest.job_id
    assert node._valid_result(result, manifest, 6882)
    assert not node._valid_result(result, manifest, 6883)
