import hashlib
import json
import re

import pytest

from bitcompute.manifest import JobManifest, WorkUnit


def make(**over):
    kw = dict(
        name="demo",
        mode="train",
        shards=[b"shard-a", b"shard-b"],
        units=[WorkUnit(uid="u1", round=0, shard="shard-a")],
        executor="local",
        params={"lr": 0.1},
        redundancy=2,
    )
    kw.update(over)
    return JobManifest.create(**kw)


def test_manifest_content_address_is_stable():
    a = make()
    b = make()
    assert re.fullmatch(r"[0-9a-f]{64}", a.job_id)
    assert a.job_id == b.job_id
    assert len(a.job_id) == 64


def test_content_address_changes_with_inputs():
    base = make()
    assert base.job_id != make(executor="ray").job_id
    assert base.job_id != make(params={"lr": 0.2}).job_id
    assert base.job_id != make(shards=[b"shard-a", b"shard-c"]).job_id
    assert base.job_id != make(name="other").job_id


def test_units_encode_rounds():
    m = make(
        units=[
            WorkUnit(uid="a", round=2, shard="shard-a"),
            WorkUnit(uid="b", round=0, shard="shard-a"),
            WorkUnit(uid="c", round=2, shard="shard-b"),
            WorkUnit(uid="d", round=1, shard="shard-b"),
        ]
    )
    assert m.rounds() == [0, 1, 2]


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        make(mode="eval")


def test_train_unit_requires_shard():
    with pytest.raises(ValueError):
        make(units=[WorkUnit(uid="u1", round=0)])


def test_infer_unit_requires_input_ref():
    with pytest.raises(ValueError):
        make(mode="infer", units=[WorkUnit(uid="u1", round=0)], shards=[])


def test_redundancy_must_be_positive():
    with pytest.raises(ValueError):
        make(redundancy=0)


def test_to_torrent_payload_roundtrips_json():
    m = make()
    payload = m.to_torrent_payload()
    assert isinstance(payload, bytes)
    data = json.loads(payload)
    assert data["job_id"] == m.job_id
    assert data["name"] == "demo"
    assert data["mode"] == "train"
    assert data["redundancy"] == 2
    assert data["shards"] == [s.hex() for s in m.shards]
    assert data["shard_names"] == m.shard_names
    assert data["units"][0]["uid"] == "u1"


def test_job_id_excluded_from_equality():
    a = make()
    b = make()
    b2 = JobManifest.create(
        name="demo",
        mode="train",
        shards=[b"shard-a", b"shard-b"],
        units=[WorkUnit(uid="u1", round=0, shard="shard-a")],
        executor="local",
        params={"lr": 0.1},
        redundancy=2,
    )
    assert a == b == b2
