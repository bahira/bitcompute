import os

import pytest

pytest.importorskip("llama_cpp", minversion="0.3")
_MODEL = r"C:\Users\Yuri\Documents\bitcompute\models\qwen2.5-0.5b-instruct-q4_k_m.gguf"
pytestmark = pytest.mark.skipif(not os.path.isfile(_MODEL), reason="gguf missing")


@pytestmark
def test_infer_llama_generates_text():
    from bitcompute.executors.infer_llama import InferLlama
    ex = InferLlama()
    out = ex.run(unit_uid="u1", shard=b"1+1=", params={"max_tokens": 8,
                                                        "temperature": 0.0})
    assert isinstance(out, bytes) and len(out) > 0


def test_infer_llama_registered():
    import bitcompute.executors.infer_llama  # noqa: F401 (registers)
    from bitcompute import executor as ex
    assert ex.registry()["infer_llama"].name == "infer_llama"
