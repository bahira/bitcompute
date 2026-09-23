"""Small, deterministic two-parameter SGD executor with bounded work."""
from __future__ import annotations

import math
import struct

from bitcompute.executor import register

MAX_SHARD_BYTES = 32 * 1024 * 1024
MAX_POINTS = 100_000
MAX_TRAINING_OPERATIONS = 10_000_000


class TrainNumpy:
    name = "train_numpy"

    def run(self, *, unit_uid, shard, params) -> bytes:
        del unit_uid  # unit identity is not part of the deterministic SGD update
        if not isinstance(shard, bytes) or len(shard) > MAX_SHARD_BYTES:
            raise ValueError(f"training shard must be bytes up to {MAX_SHARD_BYTES} bytes")
        if not isinstance(params, dict):
            raise ValueError("training params must be an object")
        raw_points = [line for line in shard.splitlines() if line.strip()]
        if not raw_points or len(raw_points) > MAX_POINTS:
            raise ValueError(f"training shard must contain 1 to {MAX_POINTS} data points")
        if len(raw_points) * 2 > MAX_TRAINING_OPERATIONS:
            raise ValueError("training input is too large")

        points: list[tuple[float, float]] = []
        try:
            for line in raw_points:
                columns = line.split(b",")
                if len(columns) != 2:
                    raise ValueError("each training row must contain exactly two columns")
                x, y = (float(value) for value in columns)
                if not math.isfinite(x) or not math.isfinite(y):
                    raise ValueError("training values must be finite numbers")
                points.append((x, y))
        except (UnicodeDecodeError, OverflowError) as exc:
            raise ValueError("training data contains invalid numeric values") from exc

        learning_rate = params.get("lr", 0.01)
        steps = params.get("steps", 100)
        if (not isinstance(learning_rate, (int, float)) or isinstance(learning_rate, bool)
                or not math.isfinite(learning_rate) or not 0 < learning_rate <= 1):
            raise ValueError("lr must be a finite number in (0, 1]")
        if (not isinstance(steps, int) or isinstance(steps, bool)
                or not 1 <= steps <= 1_000_000):
            raise ValueError("steps must be an integer between 1 and 1000000")
        if steps * len(points) > MAX_TRAINING_OPERATIONS:
            raise ValueError(
                f"training job exceeds the {MAX_TRAINING_OPERATIONS}-operation safety limit"
            )

        weight = bias = 0.0
        for _ in range(steps):
            for x, y in points:
                error = (weight * x + bias) - y
                weight -= learning_rate * error * x
                bias -= learning_rate * error
        if not math.isfinite(weight) or not math.isfinite(bias):
            raise ValueError("training diverged to a non-finite result")
        return struct.pack("<2d", weight, bias)


register(TrainNumpy)
