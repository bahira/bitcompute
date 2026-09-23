"""Durable, idempotent off-chain credit settlement for verified work.

This ledger records Bitcompute credits only. A real cash/crypto payout must be
performed by an explicitly configured PaymentProvider; the core never moves
money or stores provider credentials.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bitcompute import security

CURRENCY = "bitcompute-credit"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


class PaymentProvider(Protocol):
    """Adapter contract for a future external payment rail."""

    name: str

    def transfer(self, *, worker_id: str, amount: int, currency: str, idempotency_key: str) -> str:
        """Return the provider's durable transfer reference after successful payout."""
        ...


class CreditSettlementLedger:
    """SQLite-backed credit book with idempotent per-job/per-worker receipts."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS settlements (
                    txid TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    worker_port INTEGER NOT NULL,
                    units INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_ref TEXT,
                    receipt TEXT NOT NULL,
                    UNIQUE(job_id, worker_id)
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30.0)
        db.row_factory = sqlite3.Row
        return db

    def settle_job(
        self,
        job_id: str,
        results: Iterable[dict[str, Any]],
        *,
        credits_per_unit: int = 1,
        coordinator_key: Ed25519PrivateKey | None = None,
    ) -> list[dict[str, Any]]:
        if not job_id or len(job_id) > 128:
            raise ValueError("job_id is invalid")
        if not isinstance(credits_per_unit, int) or isinstance(credits_per_unit, bool):
            raise ValueError("credits_per_unit must be a positive integer")
        if credits_per_unit <= 0:
            raise ValueError("credits_per_unit must be a positive integer")

        receipts: list[dict[str, Any]] = []
        with self._connect() as db:
            for result in results:
                port = result["worker_port"]
                units = len(result["units"])
                if not isinstance(port, int) or isinstance(port, bool) or units <= 0:
                    raise ValueError("verified result is missing worker port or units")
                worker_id = result.get("worker_id") or f"legacy-port:{port}"
                if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 128:
                    raise ValueError("worker identity is invalid")
                amount = units * credits_per_unit
                txid = hashlib.sha256(_canonical_json({
                    "version": 1,
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "units": units,
                    "amount": amount,
                    "currency": CURRENCY,
                })).hexdigest()
                receipt: dict[str, Any] = {
                    "version": 1,
                    "txid": txid,
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "worker_port": port,
                    "units": units,
                    "amount": amount,
                    "currency": CURRENCY,
                    "status": "credit-recorded",
                }
                signed_receipt = None
                if coordinator_key is not None:
                    signed_receipt = security.sign_envelope(
                        _canonical_json(receipt), coordinator_key,
                        purpose=f"settlement:{job_id}",
                    ).decode("utf-8")
                stored = {**receipt, "signed_receipt": signed_receipt}
                db.execute(
                    """INSERT OR IGNORE INTO settlements
                       (txid, job_id, worker_id, worker_port, units, amount,
                        currency, status, provider_ref, receipt)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'credit-recorded', NULL, ?)""",
                    (txid, job_id, worker_id, port, units, amount, CURRENCY,
                     json.dumps(stored, sort_keys=True, separators=(",", ":"))),
                )
                row = db.execute(
                    "SELECT txid, job_id, worker_id, units, amount, currency, status, receipt "
                    "FROM settlements WHERE job_id=? AND worker_id=?",
                    (job_id, worker_id),
                ).fetchone()
                if row is None or row["txid"] != txid:
                    raise ValueError("conflicting settlement exists for this worker and job")
                receipts.append(json.loads(row["receipt"]))
        return receipts

    def pay_due(self, provider: PaymentProvider, *, limit: int = 100) -> list[dict[str, Any]]:
        """Pay pending credit receipts through an explicitly supplied provider.

        Provider calls use txid as the idempotency key. The ledger is marked paid
        only after a non-empty provider reference is returned.
        """
        if limit < 1:
            raise ValueError("limit must be positive")
        paid: list[dict[str, Any]] = []
        with self._connect() as db:
            rows = db.execute(
                "SELECT txid, worker_id, amount, currency FROM settlements "
                "WHERE status='credit-recorded' ORDER BY rowid LIMIT ?",
                (limit,),
            ).fetchall()
            for row in rows:
                reference = provider.transfer(
                    worker_id=row["worker_id"], amount=row["amount"],
                    currency=row["currency"], idempotency_key=row["txid"],
                )
                if not isinstance(reference, str) or not reference:
                    raise RuntimeError("payment provider returned no transfer reference")
                db.execute(
                    "UPDATE settlements SET status='paid', provider_ref=? WHERE txid=? "
                    "AND status='credit-recorded'",
                    (reference, row["txid"]),
                )
                paid.append({"txid": row["txid"], "provider_ref": reference})
        return paid

    def list_job(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT receipt, provider_ref, status FROM settlements "
                "WHERE job_id=? ORDER BY worker_port",
                (job_id,),
            ).fetchall()
        output = []
        for row in rows:
            value = json.loads(row["receipt"])
            value["status"] = row["status"]
            if row["provider_ref"]:
                value["provider_ref"] = row["provider_ref"]
            output.append(value)
        return output
