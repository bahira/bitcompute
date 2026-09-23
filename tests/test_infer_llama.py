from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest


def _module():
    import bitcompute.executors.infer_llama as infer_llama

    return infer_llama


def test_infer_llama_registered():
    infer_llama = _module()
    from bitcompute import executor

    assert executor.registry()["infer_llama"] is infer_llama.InferLlama


def test_hf_url_build():
    infer_llama = _module()
    assert infer_llama._hf_url(
        "Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q4_k_m.gguf"
    ) == (
        "https://hf.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/"
        "qwen2.5-0.5b-instruct-q4_k_m.gguf"
    )


def test_model_download_url_rejects_unsafe_identifiers():
    infer_llama = _module()
    for model in ("../owner/repo/file.gguf", "owner/repo/../file.gguf", "owner/repo/file.bin"):
        with pytest.raises(ValueError, match="model must be"):
            infer_llama._hf_url(model)


def test_infer_llama_lazy_init_and_environment_override(tmp_path, monkeypatch):
    infer_llama = _module()
    model = tmp_path / "tiny.gguf"
    model.write_bytes(b"fake model file; llama is stubbed")
    loads = []

    class FakeLlama:
        def __init__(self, *, model_path, n_ctx, verbose):
            self.model_path = model_path
            self._n_ctx = n_ctx
            loads.append(model_path)

        def __call__(self, prompt, *, max_tokens, temperature):
            assert prompt == "1+1="
            assert max_tokens == 4
            assert temperature == 0.0
            return {"choices": [{"text": "2"}]}

        def n_ctx(self):
            return self._n_ctx

    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=FakeLlama))
    monkeypatch.setenv("BITCOMPUTE_MODEL", str(model))
    executor = infer_llama.InferLlama(n_ctx=256)
    assert executor._llm is None
    assert executor.run(
        unit_uid="u", shard=b"1+1=", params={"max_tokens": 4}
    ) == b"2"
    assert executor._llm is not None
    assert executor._llm.n_ctx() == 256
    assert loads == [str(model)]


def test_real_gguf_inference():
    """Run against a real local GGUF when BITCOMPUTE_TEST_GGUF is configured."""
    model = os.environ.get("BITCOMPUTE_TEST_GGUF")
    if not model:
        pytest.skip("set BITCOMPUTE_TEST_GGUF to a GGUF file to run real inference")
    assert os.path.isfile(model), f"BITCOMPUTE_TEST_GGUF does not exist: {model}"
    pytest.importorskip("llama_cpp", minversion="0.3")

    infer_llama = _module()
    executor = infer_llama.InferLlama(n_ctx=256)
    result = executor.run(
        unit_uid="tiny-smoke",
        shard=b"Reply with one short word: hello",
        params={"model": model, "n_ctx": 256, "max_tokens": 8, "temperature": 0.0},
    )
    assert isinstance(result, bytes)
    assert result.decode("utf-8").strip()
    assert executor._llm is not None
