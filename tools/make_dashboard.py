"""Generate index.html (dashboard) from network.json. Usage: python tools/make_dashboard.py"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build() -> str:
    with open(os.path.join(ROOT, "network.json"), encoding="utf-8") as f:
        net = json.loads(f.read())
    rows = []
    for j in net["jobs"]:
        res = j["result"]
        res_str = ", ".join(f"{k}={v}" for k, v in res.items()) if isinstance(res, dict) else str(res)
        ver = ", ".join(f"{k}:{v}" for k, v in j["torrent_verified"].items())
        tok = ", ".join(f"{k}:{v['bytes']}B/{v['pieces']}p" for k, v in j["tokens"].items())
        rows.append(
            f"<tr><td>{j['name']}</td><td>{j['mode']}</td>"
            f"<td><code>{j['magnet']}</code></td><td>{j['workers']}</td>"
            f"<td>{res_str}</td><td>{ver}</td><td>{tok}</td></tr>")
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>bitcompute | network state</title>"
        "<style>body{font-family:sans-serif;margin:2rem;max-width:1100px}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px;font-size:14px}"
        "code{background:#eee;padding:1px 4px}h1{font-size:1.4rem}</style></head><body>"
        "<h1>bitcompute — network state</h1>"
        f"<p>Generated: {net['generated']} — full data: <a href='network.json'>network.json</a> · "
        "<a href='README.md'>README</a></p>"
        "<table><tr><th>Job</th><th>Mode</th><th>Magnet</th><th>Workers</th>"
        "<th>Result</th><th>Torrents verified</th><th>Tokens (bytes/pieces)</th></tr>"
        + "".join(rows) + "</table>"
        "<h2>HF model select (infer executor)</h2>"
        "<select id='m' style='padding:4px'>"
        "<option value='Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q4_k_m.gguf' selected>"
        "Qwen2.5-0.5B-Instruct q4_k_m (default)</option>"
        "<option value='Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q4_0.gguf'>"
        "q4_0</option>"
        "<option value='Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q5_k_m.gguf'>"
        "q5_k_m</option>"
        "<option value='Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q8_0.gguf'>"
        "q8_0</option>"
        "</select>"
        "<pre id='j' style='background:#f6f8fa;padding:8px;border:1px solid #ccc'></pre>"
        "<script>const M=["
        "['q4_k_m','Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q4_k_m.gguf'],"
        "['q4_0','Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q4_0.gguf'],"
        "['q5_k_m','Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q5_k_m.gguf'],"
        "['q8_0','Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q8_0.gguf']];"
        "function upd(){const i=M.findIndex(m=>m[1]===document.getElementById('m').value);"
        "document.getElementById('j').textContent="
        "JSON.stringify({model:M[i][1],n_ctx:512,max_tokens:64,temperature:0},null,1);} "
        "document.getElementById('m').onchange=upd;upd();</script></body></html>")


if __name__ == "__main__":
    out = os.path.join(ROOT, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(build())
    print("wrote", out)
