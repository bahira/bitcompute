import struct

from bitcompute.executor import register


class TrainNumpy:
    name = "train_numpy"

    def run(self, *, unit_uid, shard, params) -> bytes:
        pts = [tuple(map(float, ln.split(b","))) for ln in shard.strip().splitlines()]
        lr = params.get("lr", 0.01)
        steps = params.get("steps", 100)
        w = b = 0.0
        for _ in range(steps):
            for x, y in pts:
                err = (w * x + b) - y
                w -= lr * err * x
                b -= lr * err
        return struct.pack("<2d", w, b)


register(TrainNumpy)
