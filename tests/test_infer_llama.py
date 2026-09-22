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


@pytestmark
def test_infer_llama_lazy_init():
    from bitcompute.executors.infer_llama import InferLlama
    ex = InferLlama(n_ctx=512)
    assert ex._llm is None  # not yet constructed
    ex.run(unit_uid="u", shard=b"1+1=", params={"max_tokens": 4})
    assert ex._llm is not None
    assert ex._llm.n_ctx() == 512


@pytestmark
def test_infer_llama_env_model_override(monkeypatch):
    from bitcompute.executors.infer_llama import InferLlama, _MODEL_PATH
    monkeypatch.setenv("BITCOMPUTE_MODEL", _MODEL_PATH)
    ex = InferLlama()
    ex.run(unit_uid="u", shard=b"hi", params={"max_tokens": 4})
    assert ex._llm.model_path == _MODEL_PATH


def test_hf_url_build():
    from bitcompute.executors.infer_llama import _hf_url
    assert _hf_url(
        "Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q4_k_m.gguf") == (
        "https://hf.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/"
        "qwen2.5-0.5b-instruct-q4_k_m.gguf")
