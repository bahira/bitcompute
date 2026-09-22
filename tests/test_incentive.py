from bitcompute.incentive import Ledger, should_unchoke


def test_only_contributors_get_unchoked():
    led = Ledger()
    led.record(peer="A", contributed=10000, received=1000)
    led.record(peer="B", contributed=0, received=9000)
    led.record(peer="C", contributed=5000, received=5000)
    open_slots = should_unchoke(led, max_unchoked=2)
    assert "B" not in open_slots
    assert "A" in open_slots
    assert len(open_slots) == 2


def test_optimistic_unchoke_reserves_one_slot():
    led = Ledger()
    led.record(peer="A", contributed=99, received=1)
    assert len(should_unchoke(led, max_unchoked=2)) == 1
    led.record(peer="B", contributed=5, received=10)
    assert len(should_unchoke(led, max_unchoked=2)) == 2


def test_max_zero_empty():
    led = Ledger()
    led.record(peer="A", contributed=1, received=0)
    assert should_unchoke(led, max_unchoked=0) == []


def test_empty_ledger_empty():
    assert should_unchoke(Ledger(), max_unchoked=3) == []


def test_record_accumulates():
    led = Ledger()
    led.record("A", 10, 1)
    led.record("A", 5, 2)
    assert led.peers["A"] == [15, 3]
    assert led.net("A") == 12
