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
        + "".join(rows) + "</table></body></html>")


if __name__ == "__main__":
    out = os.path.join(ROOT, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(build())
    print("wrote", out)
