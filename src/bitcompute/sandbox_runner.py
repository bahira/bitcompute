"""Private child-process entry point used only inside the bubblewrap sandbox."""
from __future__ import annotations

import argparse
import base64
import contextlib
import importlib
import json
import os
import sys
from typing import Any

MAX_INPUT_BYTES = 48 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024


def _apply_limits() -> None:
    try:
        import resource
    except ImportError as exc:  # pragma: no cover - Linux bwrap always has resource
        raise RuntimeError("resource limits are not available") from exc
    memory_limit = 1024 * 1024 * 1024
    file_limit = MAX_OUTPUT_BYTES
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def _load_executor(module_name: str, qualname: str) -> Any:
    if not module_name or "<locals>" in qualname or "__" in module_name:
        raise ValueError("invalid custom executor target")
    value: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        value = getattr(value, part)
    return value()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--module", required=True)
    parser.add_argument("--qualname", required=True)
    args = parser.parse_args()
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        print("sandbox request too large", file=sys.stderr)
        return 2
    try:
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        params = request["params"]
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        shard_value = request.get("shard")
        shard = (
            base64.b64decode(shard_value, validate=True)
            if shard_value is not None else None
        )
        if shard is not None and len(shard) > MAX_INPUT_BYTES:
            raise ValueError("shard too large")
        _apply_limits()
        executor = _load_executor(args.module, args.qualname)
        with open(os.devnull, "w", encoding="utf-8") as sink:
            response_fd = os.dup(1)
            os.dup2(sink.fileno(), 1)
            try:
                with contextlib.redirect_stdout(sink):
                    result = executor.run(
                        unit_uid=request["unit_uid"], shard=shard, params=params
                    )
            finally:
                os.dup2(response_fd, 1)
                os.close(response_fd)
        if not isinstance(result, bytes):
            raise TypeError("custom executors must return bytes")
        if len(result) > MAX_OUTPUT_BYTES:
            raise ValueError("custom executor result too large")
        response = json.dumps(
            {"result": base64.b64encode(result).decode("ascii")},
            separators=(",", ":"),
        ).encode("ascii")
        if len(response) > MAX_OUTPUT_BYTES:
            raise ValueError("custom executor response too large")
        sys.stdout.buffer.write(response)
        return 0
    except Exception as exc:  # noqa: BLE001 - child must report failures to parent
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
