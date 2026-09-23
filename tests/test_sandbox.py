from __future__ import annotations

import shutil
import sys

import pytest

from bitcompute import sandbox


def test_custom_executor_fails_closed_without_bubblewrap(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="requires bubblewrap"):
        sandbox.run_custom_executor(
            "installed_plugin", "Plugin", unit_uid="u", shard=b"input", params={}
        )


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("bwrap") is None,
                    reason="bubblewrap is not available on this host")
def test_bubblewrap_runs_executor_in_child_process():
    result = sandbox.run_custom_executor(
        "bitcompute.executors.train_numpy",
        "TrainNumpy",
        unit_uid="u",
        shard=b"0,1\\n1,3\\n",
        params={"steps": 2},
    )
    assert len(result) == 16
