from __future__ import annotations

import os
import urllib.request

from bitcompute.executor import register

_MODEL_PATH = r"C:\Users\Yuri\Documents\bitcompute\models\qwen2.5-0.5b-instruct-q4_k_m.gguf"
_DEFAULT_N_CTX = 512
HF = "https://hf.co"


def _hf_url(model: str) -> str:
    """"owner/repo/file.gguf" -> HF resolve URL."""
    parts = model.split("/")
    return f"{HF}/{'/'.join(parts[:-1])}/resolve/main/{parts[-1]}"


def _model_path(model: str) -> str:
    """Local file as-is; HF id 'owner/repo/file.gguf' cached once into models/."""
    if os.path.isfile(model):
        return model
    parts = model.split("/")
    if len(parts) >= 3 and "." in parts[-1]:
        fname = parts[-1]
        dest = os.path.join(os.path.dirname(_MODEL_PATH) or "models", fname)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not os.path.isfile(dest):
            urllib.request.urlretrieve(_hf_url(model), dest)
        return dest
    return model


@register
class InferLlama:
    name = "infer_llama"

    def __init__(self, n_ctx: int | None = None):
        self._n_ctx = n_ctx or _DEFAULT_N_CTX
        self._llm = None

    def _ensure(self, model: str, n_ctx: int) -> None:
        if self._llm is None:
            from llama_cpp import Llama
            self._llm = Llama(model_path=_model_path(model), n_ctx=n_ctx, verbose=False)

    def run(self, *, unit_uid, shard, params) -> bytes:
        n_ctx = params.get("n_ctx") or self._n_ctx or _DEFAULT_N_CTX
        model = (params.get("model")
                 or os.environ.get("BITCOMPUTE_MODEL")
                 or _MODEL_PATH)
        self._ensure(model, n_ctx)
        max_tokens = params.get("max_tokens", 64)
        temperature = params.get("temperature", 0.0)
        prompt = (shard or b"").decode("utf-8") or "ping"
        out = self._llm(prompt, max_tokens=max_tokens, temperature=temperature)
        return out["choices"][0]["text"].encode("utf-8")
