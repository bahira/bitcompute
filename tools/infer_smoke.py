from bitcompute.executors.infer_llama import InferLlama

ex = InferLlama()
b = ex.run(unit_uid="u1", shard=b"ping", params={})
print(b.decode("utf-8"))
assert b.strip(), "empty generation"
