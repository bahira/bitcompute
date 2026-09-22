from llama_cpp import Llama
llm = Llama(model_path=r"C:\Users\Yuri\Documents\bitcompute\models\qwen2.5-0.5b-instruct-q4_k_m.gguf", n_ctx=512, verbose=False)
out = llm("Say hello in one word.", max_tokens=16, temperature=0)
text = out["choices"][0]["text"]
print(text)
assert text.strip(), "empty generation"
