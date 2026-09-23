"""Fail-closed bubblewrap sandbox for locally installed custom executors.

The worker never executes arbitrary executor code in its main process. Custom
plugins must be installed inside the active Python environment and run with a
read-only filesystem, no network namespace, bounded CPU/address-space/file size,
and a short wall-clock timeout. Built-in executors remain in-process.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MAX_REQUEST_BYTES = 48 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
EXECUTOR_TIMEOUT_SECONDS = 35


def _sandbox_command(module_name: str, qualname: str) -> list[str]:
    if sys.platform != "linux":
        raise RuntimeError("custom executor sandbox is currently supported only on Linux")
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise RuntimeError(
            "custom executor requires bubblewrap (bwrap); install it or use a bundled executor"
        )
    identifier = r"[A-Za-z_][A-Za-z0-9_]*"
    if (
        not module_name
        or "__" in module_name
        or any(not re.fullmatch(identifier, part) for part in module_name.split("."))
        or not qualname
        or "__" in qualname
        or any(not re.fullmatch(identifier, part) for part in qualname.split("."))
    ):
        raise ValueError("invalid custom executor import target")

    runtime = Path(sys.prefix).resolve()
    try:
        python_relative = Path(sys.executable).relative_to(Path(sys.prefix))
    except ValueError as exc:
        raise RuntimeError("Python executable is outside its configured environment") from exc

    source_root = Path(__file__).resolve().parent.parent
    command = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--dir", "/work",
        "--dir", "/opt",
        "--dir", "/etc",
        "--ro-bind", str(source_root), "/opt/bitcompute-source",
        "--ro-bind", str(runtime), "/opt/runtime",
        "--setenv", "HOME", "/tmp/home",
        "--dir", "/tmp/home",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "PYTHONPATH", "/opt/bitcompute-source",
        "--setenv", "PYTHONNOUSERSITE", "1",
        "--chdir", "/work",
    ]
    for host_path in (Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64")):
        if host_path.exists():
            command.extend(["--ro-bind", str(host_path), str(host_path)])
    loader_cache = Path("/etc/ld.so.cache")
    if loader_cache.is_file():
        command.extend(["--ro-bind", str(loader_cache), str(loader_cache)])
    command.extend([
        f"/opt/runtime/{python_relative.as_posix()}",
        "-m", "bitcompute.sandbox_runner",
        "--module", module_name,
        "--qualname", qualname,
    ])
    return command


def run_custom_executor(
    module_name: str,
    qualname: str,
    *,
    unit_uid: str,
    shard: bytes | None,
    params: dict[str, Any],
) -> bytes:
    """Run a plugin target without importing its code in the worker process."""
    command = _sandbox_command(module_name, qualname)
    payload = json.dumps({
        "unit_uid": unit_uid,
        "shard": base64.b64encode(shard).decode("ascii") if shard is not None else None,
        "params": params,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("custom executor request exceeds the sandbox size limit")
    try:
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            completed = subprocess.run(
                command,
                input=payload,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=EXECUTOR_TIMEOUT_SECONDS,
                check=False,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONNOUSERSITE": "1"},
            )
            stdout_file.seek(0)
            output = stdout_file.read(MAX_OUTPUT_BYTES + 1)
            stderr_file.seek(0, 2)
            stderr_size = stderr_file.tell()
            stderr_file.seek(max(0, stderr_size - 2000))
            diagnostics = stderr_file.read(2000).decode("utf-8", errors="replace").strip()
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"custom executor exceeded {EXECUTOR_TIMEOUT_SECONDS}s sandbox timeout"
        ) from exc
    if completed.returncode != 0:
        suffix = f": {diagnostics}" if diagnostics else ""
        raise RuntimeError(
            f"sandboxed custom executor failed with exit status {completed.returncode}{suffix}"
        )
    if len(output) > MAX_OUTPUT_BYTES:
        raise ValueError("custom executor output exceeds the sandbox size limit")
    try:
        response = json.loads(output.decode("utf-8"))
        result = base64.b64decode(response["result"], validate=True)
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("sandboxed custom executor returned an invalid response") from exc
    if len(result) > MAX_OUTPUT_BYTES:
        raise ValueError("custom executor result exceeds the sandbox size limit")
    return result
