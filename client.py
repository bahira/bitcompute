"""Single-file Tkinter client + console entry for bitcompute.

Usage:
  python client.py seed job --port 7401 --workers 7402 7403
  python client.py worker --magnet <hex> --job-dir job --port 7402 --seed-port 7401
  python client.py status job
  python client.py                     (starts the Tk UI)
"""
from __future__ import annotations

import json
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from bitcompute import node  # noqa: E402

_HF_MODELS = {
    "q4_k_m (default)": "Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q4_k_m.gguf",
    "q4_0": "Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q4_0.gguf",
    "q5_k_m": "Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q5_k_m.gguf",
    "q8_0": "Qwen/Qwen2.5-0.5B-Instruct-GGUF/qwen2.5-0.5b-instruct-q8_0.gguf",
}


def run_seed(job_dir, port, workers):
    return node.seed_job(job_dir, port=port, worker_ports=tuple(workers))


def run_worker_job(magnet, job_dir, port, seed_port):
    return node.run_worker(magnet, job_dir, port, seed_port)


def run_status(job_dir):
    return node.status(job_dir)


def _console(argv):
    p = __import__("argparse").ArgumentParser(prog="bitcompute")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("job_dir")
    s.add_argument("--port", type=int, default=7401)
    s.add_argument("--workers", type=int, nargs="+", default=[7402, 7403])
    w = sub.add_parser("worker")
    w.add_argument("--magnet", required=True)
    w.add_argument("--job-dir", required=True)
    w.add_argument("--port", type=int, default=7402)
    w.add_argument("--seed-port", type=int, default=7401)
    st = sub.add_parser("status")
    st.add_argument("job_dir")
    a = p.parse_args(argv)
    if a.cmd == "seed":
        print(json.dumps(run_seed(a.job_dir, a.port, a.workers)))
    elif a.cmd == "worker":
        print(json.dumps(run_worker_job(a.magnet, a.job_dir, a.port, a.seed_port)))
    else:
        print(json.dumps(run_status(a.job_dir)))
    return 0


def _ui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("bitcompute — p2p compute client")
    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=8, pady=8)

    out = ttk.Frame(root)
    out.pack(fill="both", expand=True)
    txt = tk.Text(out, height=18)
    txt.pack(fill="both", expand=True)
    txt.insert("1.0", "Ready.\n")

    def log(msg):
        txt.insert("end", str(msg) + "\n")
        txt.see("end")

    def job(title, fn):
        def work():
            try:
                res = fn()
                root.after(0, lambda: log(json.dumps(res, indent=1)))
            except Exception as e:  # noqa: BLE001
                root.after(0, lambda: log(f"ERROR: {e}"))
        threading.Thread(target=work, daemon=True).start()

    # ---- Seed tab ----
    f1 = ttk.Frame(nb)
    nb.add(f1, text="Seed")
    ttk.Label(f1, text="job dir").grid(row=0, column=0, sticky="w")
    seed_dir = ttk.Entry(f1); seed_dir.insert(0, "job"); seed_dir.grid(row=0, column=1)
    ttk.Label(f1, text="seed port").grid(row=1, column=0, sticky="w")
    seed_port = ttk.Entry(f1); seed_port.insert(0, "7401"); seed_port.grid(row=1, column=1)
    ttk.Label(f1, text="workers (espace)").grid(row=2, column=0, sticky="w")
    seed_w = ttk.Entry(f1); seed_w.insert(0, "7402 7403"); seed_w.grid(row=2, column=1)
    ttk.Button(f1, text="Run seed", command=lambda: job(
        "seed", lambda: run_seed(seed_dir.get(), int(seed_port.get()),
                                [int(x) for x in seed_w.get().split()])))\
        .grid(row=3, column=0, pady=6)

    # ---- Worker tab ----
    f2 = ttk.Frame(nb)
    nb.add(f2, text="Worker")
    ttk.Label(f2, text="magnet (40 hex)").grid(row=0, column=0, sticky="w")
    w_mag = ttk.Entry(f2, width=48); w_mag.grid(row=0, column=1)
    ttk.Label(f2, text="job dir").grid(row=1, column=0, sticky="w")
    w_dir = ttk.Entry(f2); w_dir.insert(0, "job"); w_dir.grid(row=1, column=1)
    ttk.Label(f2, text="port / seed port").grid(row=2, column=0, sticky="w")
    w_port = ttk.Entry(f2, width=10); w_port.insert(0, "7402"); w_port.grid(row=2, column=1)
    w_sport = ttk.Entry(f2, width=10); w_sport.insert(0, "7401"); w_sport.grid(row=2, column=2)
    ttk.Label(f2, text="hf model").grid(row=3, column=0, sticky="w")
    cb = ttk.Combobox(f2, values=list(_HF_MODELS), width=60)
    cb.current(0); cb.grid(row=3, column=1, columnspan=2)
    def run_w():
        model = _HF_MODELS[cb.get()]
        jj = os.path.join(w_dir.get(), "job.json")
        if os.path.isfile(jj):
            with open(jj, encoding="utf-8") as f:
                spec = json.loads(f.read())
            spec.setdefault("params", {})["model"] = model
            with open(jj, "w", encoding="utf-8") as f:
                f.write(json.dumps(spec))
        return run_worker_job(w_mag.get(), w_dir.get(), int(w_port.get()), int(w_sport.get()))
    ttk.Button(f2, text="Run worker", command=lambda: job("worker", run_w))\
        .grid(row=4, column=0, pady=6)

    # ---- Status tab ----
    f3 = ttk.Frame(nb)
    nb.add(f3, text="Status")
    ttk.Label(f3, text="job dir").grid(row=0, column=0, sticky="w")
    s_dir = ttk.Entry(f3); s_dir.insert(0, "job"); s_dir.grid(row=0, column=1)
    ttk.Button(f3, text="Show summary", command=lambda: job(
        "status", lambda: run_status(s_dir.get()))).grid(row=1, column=0, pady=6)

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(_console(sys.argv[1:]) if len(sys.argv) > 1 else _ui())
