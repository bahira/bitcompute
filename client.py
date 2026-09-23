"""Single-file client for bitcompute: console subcommands + Tk UI.

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


def _parse_worker_key_specs(value):
    parsed = {}
    for item in value.split():
        port_text, separator, path = item.partition("=")
        if not separator or not port_text or not path:
            raise ValueError("worker public keys must use PORT=PATH format")
        port = int(port_text)
        if port in parsed:
            raise ValueError(f"duplicate worker key for port {port}")
        parsed[port] = path
    return parsed


def run_seed(
    job_dir, port, workers, on_ready=None,
    identity_key=None, worker_public_keys=None, encryption_key=None,
):
    if not identity_key or not worker_public_keys or not encryption_key:
        raise ValueError("secure seed mode requires all three key inputs")
    return node.seed_job(
        job_dir, port=port, worker_ports=tuple(workers), on_ready=on_ready,
        identity_key=identity_key, worker_public_keys=worker_public_keys,
        encryption_key=encryption_key,
    )


def run_worker_job(
    magnet, job_dir, port, seed_port, seed_host="127.0.0.1", announce_port=None,
    identity_key=None, trusted_seed_key=None, encryption_key=None,
):
    if not identity_key or not trusted_seed_key or not encryption_key:
        raise ValueError("secure worker mode requires all three key inputs")
    return node.run_worker(
        magnet, job_dir, port, seed_port,
        seed_host=seed_host, announce_port=announce_port,
        identity_key=identity_key, trusted_seed_key=trusted_seed_key,
        encryption_key=encryption_key,
    )


def run_status(job_dir):
    return node.status(job_dir)


def _console(argv):
    from bitcompute.cli import main

    return main(argv)


# ---------------------------------------------------------------- UI

_BG = "#0d1115"
_CARD = "#151b21"
_ACC = "#00e5a0"
_TX = "#e8eef2"
_MUT = "#9fb2bf"
_FONT = "Segoe UI"
_MONO = "Consolas"


def _style(s):
    s.theme_use("clam")
    s.configure(".", font=(_FONT, 10), background=_BG, foreground=_TX,
                borderwidth=0, padding=6)
    s.configure("TFrame", background=_BG)
    s.configure("Card.TFrame", background=_CARD, padding=10, relief="flat")
    s.configure("TLabel", background=_BG, foreground=_TX)
    s.configure("Mut.TLabel", foreground=_MUT)
    s.configure("H.TLabel", font=(_FONT, 14, "bold"), foreground=_ACC)
    s.configure("TNotebook", background=_BG, tabpadded=14)
    s.map("TNotebook",
          background=[("selected", _ACC), ("!selected", _CARD)],
          foreground=[("selected", "#06100b"), ("!selected", _MUT)])
    s.configure("TNotebook.Tab", font=(_FONT, 10, "bold"), padding=(12, 6))
    s.configure("TEntry", font=(_MONO, 10), fieldbackground="#1c242b",
                insertcolor=_ACC, padding=4)
    s.map("TEntry", fieldbackground=[("focus", "#222c34")])
    s.configure("TCombobox", font=(_MONO, 10), fieldbackground="#1c242b",
                arrowcolor=_MUT, padding=4)
    s.map("TCombobox", fieldbackground=[("focus", "#222c34")])
    s.configure("TButton", font=(_FONT, 10, "bold"), background=_ACC,
                foreground="#06100b", padding=(14, 7), relief="flat")
    s.map("TButton", background=[("active", "#3ef0b4"), ("pressed", "#00c78d")])
    s.configure("TScrollbAr", background=_CARD, troughcolor=_BG,
                lightcolor=_ACC)
    s.configure("Horizontal.TProgressbar", background=_CARD, troughcolor="#1c242b",
                lightcolor=_ACC, borderwidth=0)


def _ui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("bitcompute — p2p compute client")
    root.configure(bg=_BG)
    _style(ttk.Style(root))

    top = ttk.Frame(root)
    top.pack(fill="x", padx=14, pady=(12, 2))
    ttk.Label(top, text="bitcompute", style="H.TLabel").pack(side="left")
    ttk.Label(top, text="secure swarm client", style="Mut.TLabel").pack(side="right")

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=14, pady=6)

    out = tk.Text(root, height=16, bg=_CARD, fg=_TX, insertbackground=_ACC,
                  relief="flat", font=(_MONO, 10), padx=10, pady=8)
    out.pack(fill="both", expand=False, padx=14, pady=(0, 12))
    out.insert("1.0", "ready\n")

    def log(msg):
        out.insert("end", str(msg) + "\n")
        out.see("end")

    def job(fn):
        def work():
            try:
                res = fn()
                root.after(0, lambda: log(json.dumps(res, indent=1)))
            except Exception as exc:  # noqa: BLE001
                root.after(0, lambda error=exc: log(f"error: {error}"))
        threading.Thread(target=work, daemon=True).start()

    def field(parent, row, label, default="", width=34):
        ttk.Label(parent, text=label, style="Mut.TLabel")\
            .grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        e = ttk.Entry(parent, width=width)
        if default:
            e.insert(0, default)
        e.grid(row=row, column=1, sticky="we", pady=4)
        return e

    # Seed tab
    f1 = ttk.Frame(nb, style="Card.TFrame")
    nb.add(f1, text="  Seed  ")
    sd = field(f1, 0, "job dir", "job")
    sp = field(f1, 1, "seed port", "7401", 10)
    sw = field(f1, 2, "workers", "7402 7403", 20)
    si = field(f1, 3, "seed private key", width=46)
    se = field(f1, 4, "shared AES key", width=46)
    sk = field(f1, 5, "worker keys PORT=PUBLIC_KEY", width=60)
    ttk.Button(f1, text="Run secure seed", command=lambda: job(
        lambda: run_seed(
            sd.get(), int(sp.get()), [int(x) for x in sw.get().split()],
            lambda event: root.after(
                0, lambda: log("ready: " + json.dumps(event))
            ),
            identity_key=si.get(),
            worker_public_keys=_parse_worker_key_specs(sk.get()),
            encryption_key=se.get(),
        )))\
        .grid(row=6, column=0, sticky="w", pady=10)

    # Worker tab
    f2 = ttk.Frame(nb, style="Card.TFrame")
    nb.add(f2, text="  Worker  ")
    wm = field(f2, 0, "magnet (40 hex)", width=46)
    wd = field(f2, 1, "job dir", "job")
    wp = field(f2, 2, "port", "7402", 10)
    wh = field(f2, 3, "seed host", "127.0.0.1", 24)
    ws = field(f2, 4, "seed port", "7401", 10)
    wa = field(f2, 5, "announce port", "8401", 10)
    wi = field(f2, 6, "worker private key", width=46)
    wk = field(f2, 7, "trusted seed public key", width=46)
    we = field(f2, 8, "shared AES key", width=46)
    ttk.Label(
        f2, text="Executor and model are pinned in the signed job manifest.",
        style="Mut.TLabel",
    ).grid(row=9, column=1, sticky="w", pady=4)

    def run_w():
        return run_worker_job(
            wm.get(), wd.get(), int(wp.get()), int(ws.get()),
            wh.get(), int(wa.get()),
            identity_key=wi.get(), trusted_seed_key=wk.get(), encryption_key=we.get(),
        )

    ttk.Button(f2, text="Run secure worker", command=lambda: job(run_w))\
        .grid(row=10, column=0, sticky="w", pady=10)

    # Status tab
    f3 = ttk.Frame(nb, style="Card.TFrame")
    nb.add(f3, text="  Status  ")
    std = field(f3, 0, "job dir", "job")
    ttk.Button(f3, text="Show summary", command=lambda: job(
        lambda: run_status(std.get()))).grid(row=1, column=0, sticky="w", pady=10)
    bar = ttk.Progressbar(f3, orient="horizontal", length=220, maximum=8)
    bar.grid(row=2, column=0, columnspan=2, sticky="we", pady=(6, 0))
    lbl = ttk.Label(f3, text="", style="Mut.TLabel")
    lbl.grid(row=3, column=0, columnspan=2, sticky="w")

    seen = {"n": 0}

    def poll():
        s = run_status(std.get())
        n = len([f for f in os.listdir(std.get())
                 if f.startswith("result_") and f.endswith(".json")]) \
            if os.path.isdir(std.get()) else 0
        bar["value"] = min(n, 8)
        done = "workers" in s
        lbl.config(text=f"results {n} · "
                       f"{'complete' if done else s.get('state', 'in_progress')}")
        if done and seen["n"] != n:
            seen["n"] = n
            log(json.dumps(s, indent=1))
        if not done or seen["n"] == 0:
            root.after(2000, poll)
        else:
            root.after(2000, poll)

    root.after(600, poll)

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(_console(sys.argv[1:]) if len(sys.argv) > 1 else _ui())
