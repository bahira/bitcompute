from __future__ import annotations

import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from bitcompute.executor import register

_DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q4_k_m.gguf"
_MODEL_DIR = Path(os.environ.get("BITCOMPUTE_MODEL_DIR", Path.home() / ".cache" / "bitcompute" / "models"))
_MODEL_PATH = str(_MODEL_DIR / _DEFAULT_MODEL.rsplit("/", 1)[-1])
_DEFAULT_N_CTX = 512
HF = "https://hf.co"


def _hf_url(model: str) -> str:
    """Convert an ``owner/repository/file.gguf`` identifier to a safe HF URL."""
    parts = model.split("/")
    if len(parts) != 3 or not all(parts) or not parts[-1].lower().endswith(".gguf"):
        raise ValueError("model must be a local path or owner/repository/file.gguf")
    return f"{HF}/{'/'.join(urllib.parse.quote(part, safe='.-_') for part in parts[:2])}/resolve/main/{urllib.parse.quote(parts[2], safe='.-_')}"


def _model_path(model: str) -> str:
    """Return a local model or atomically download and cache a Hugging Face model."""
    local = Path(model).expanduser()
    if local.is_file():
        return str(local)
    parts = model.split("/")
    if len(parts) == 3 and parts[-1].lower().endswith(".gguf"):
        model_dir = Path(os.environ.get("BITCOMPUTE_MODEL_DIR", _MODEL_DIR)).expanduser()
        model_dir.mkdir(parents=True, exist_ok=True)
        destination = model_dir / parts[-1]
        if not destination.is_file():
            fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=model_dir)
            os.close(fd)
            try:
                urllib.request.urlretrieve(_hf_url(model), temporary)
                if os.path.getsize(temporary) == 0:
                    raise OSError("downloaded model is empty")
                os.replace(temporary, destination)
            except BaseException:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
        return str(destination)
    raise FileNotFoundError(f"model not found: {model}")


@register
class InferLlama:
    name = "infer_llama"

    def __init__(self, n_ctx: int | None = None):
        self._n_ctx = n_ctx or _DEFAULT_N_CTX
        self._llm = None
        self._loaded_model: str | None = None

    def _ensure(self, model: str, n_ctx: int) -> None:
        resolved = _model_path(model)
        if self._llm is not None and self._loaded_model != resolved:
            raise ValueError("one executor instance cannot switch models")
        if self._llm is None:
            from llama_cpp import Llama
            self._llm = Llama(model_path=resolved, n_ctx=n_ctx, verbose=False)
            self._loaded_model = resolved

    def run(self, *, unit_uid, shard, params) -> bytes:
        n_ctx = int(params.get("n_ctx") or self._n_ctx or _DEFAULT_N_CTX)
        max_tokens = int(params.get("max_tokens", 64))
        temperature = float(params.get("temperature", 0.0))
        if not 128 <= n_ctx <= 1_048_576:
            raise ValueError("n_ctx must be between 128 and 1048576")
        if not 1 <= max_tokens <= n_ctx:
            raise ValueError("max_tokens must be between 1 and n_ctx")
        if not 0.0 <= temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        model = params.get("model") or os.environ.get("BITCOMPUTE_MODEL") or _DEFAULT_MODEL
        if not isinstance(model, str):
            raise ValueError("model must be a string")
        self._ensure(model, n_ctx)
        prompt = (shard or b"").decode("utf-8", errors="strict") or "ping"
        output = self._llm(prompt, max_tokens=max_tokens, temperature=temperature)
        return output["choices"][0]["text"].encode("utf-8")
