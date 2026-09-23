from __future__ import annotations

import json

from bitcompute import security
from bitcompute.settlement import CURRENCY, CreditSettlementLedger


class _TestProvider:
    name = "test-provider"

    def __init__(self):
        self.calls = []

    def transfer(self, *, worker_id, amount, currency, idempotency_key):
        self.calls.append((worker_id, amount, currency, idempotency_key))
        return f"test:{idempotency_key}"


def test_idempotent_credit_settlement_and_signed_receipt(tmp_path):
    private, public, _ = security.generate_identity(tmp_path / "coordinator.key")
    signing_key = security.load_private_key(private)
    public_key = security.load_public_key(public)
    ledger_path = tmp_path / "settlement.sqlite3"
    ledger = CreditSettlementLedger(ledger_path)
    results = [{
        "worker_port": 7402,
        "worker_id": "ed25519:abc123",
        "units": {"u1": "00", "u2": "11"},
    }]

    first = ledger.settle_job("job-1", results, credits_per_unit=7, coordinator_key=signing_key)
    second = ledger.settle_job("job-1", results, credits_per_unit=7, coordinator_key=signing_key)
    assert first == second
    assert first[0]["amount"] == 14
    assert first[0]["currency"] == CURRENCY
    assert first[0]["status"] == "credit-recorded"
    assert len(first[0]["txid"]) == 64

    receipt_bytes, _, _ = security.verify_envelope(
        first[0]["signed_receipt"].encode(),
        purpose="settlement:job-1",
        trusted_public_key=public_key,
        require_encryption=False,
    )
    assert json.loads(receipt_bytes)["txid"] == first[0]["txid"]
    assert ledger.list_job("job-1") == first


def test_provider_payout_is_idempotent_and_marks_receipt_paid(tmp_path):
    ledger = CreditSettlementLedger(tmp_path / "settlement.sqlite3")
    ledger.settle_job("job-2", [{"worker_port": 7403, "units": {"u1": "ab"}}])
    provider = _TestProvider()

    paid = ledger.pay_due(provider)
    assert len(paid) == 1
    assert provider.calls[0][1:3] == (1, CURRENCY)
    assert provider.calls[0][3] == paid[0]["txid"]
    assert ledger.pay_due(provider) == []
    receipt = ledger.list_job("job-2")[0]
    assert receipt["status"] == "paid"
    assert receipt["provider_ref"] == paid[0]["provider_ref"]
