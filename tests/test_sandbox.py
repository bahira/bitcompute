from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from bitcompute import sandbox


def test_custom_executor_fails_closed_without_bubblewrap(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    message = "only on Linux" if sys.platform != "linux" else "requires bubblewrap"
    with pytest.raises(RuntimeError, match=message):
        sandbox.run_custom_executor(
            "installed_plugin", "Plugin", unit_uid="u", shard=b"input", params={}
        )


def _bubblewrap_probe_failure() -> str | None:
    if sys.platform != "linux":
        return "bubblewrap is supported only on Linux"
    if shutil.which("bwrap") is None:
        return "bubblewrap is not installed"
    try:
        command = sandbox._sandbox_command(
            "bitcompute.executors.train_numpy", "TrainNumpy"
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return str(exc)
    runner_index = command.index("-m")
    command = [*command[:runner_index], "/usr/bin/true"]
    try:
        result = subprocess.run(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if result.returncode:
        return result.stderr.decode("utf-8", errors="replace")[-500:].strip() or (
            f"bubblewrap exited with status {result.returncode}"
        )
    return None


def test_bubblewrap_runs_executor_in_child_process():
    failure = _bubblewrap_probe_failure()
    if failure is not None:
        pytest.skip(f"bubblewrap sandbox cannot run on this host: {failure}")
    result = sandbox.run_custom_executor(
        "bitcompute.executors.train_numpy",
        "TrainNumpy",
        unit_uid="u",
        shard=b"0,1\\n1,3\\n",
        params={"steps": 2},
    )
    assert len(result) == 16
