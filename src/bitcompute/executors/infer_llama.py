from __future__ import annotations

from bitcompute.executor import register

_MODEL_PATH = r"C:\Users\Yuri\Documents\bitcompute\models\qwen2.5-0.5b-instruct-q4_k_m.gguf"


@register
class InferLlama:
    name = "infer_llama"

    def __init__(self):
        from llama_cpp import Llama

        self._llm = Llama(model_path=_MODEL_PATH, n_ctx=512, verbose=False)

    def run(self, *, unit_uid, shard, params) -> bytes:
        max_tokens = params.get("max_tokens", 64)
        temperature = params.get("temperature", 0.0)
        prompt = (shard or b"").decode("utf-8") or "ping"
        out = self._llm(prompt, max_tokens=max_tokens, temperature=temperature)
        return out["choices"][0]["text"].encode("utf-8")
