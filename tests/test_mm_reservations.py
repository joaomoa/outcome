from decimal import Decimal

import pytest

from helpers import get_balance, parlay_capital, quote_both_legs, submit_two_leg_request
from rfq_engine.errors import InsufficientFundsError


def test_quote_does_not_move_mm_funds(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))

    mm = get_balance(conn, participants["mm1"])
    assert mm["available"] == Decimal("10000")
    assert mm["locked"] == Decimal("0")


def test_both_sides_lock_on_accept(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.30"))
    engine.run_matching(request_id)

    _, premium, collateral = parlay_capital(
        [(Decimal("100"), Decimal("0.40")), (Decimal("200"), Decimal("0.30"))]
    )

    engine.accept(request_id)

    req = get_balance(conn, participants["requester"])
    mm = get_balance(conn, participants["mm1"])
    assert req["locked"] == premium
    assert req["available"] == Decimal("10000") - premium
    assert mm["locked"] == collateral
    assert mm["available"] == Decimal("10000") - collateral


def test_losing_mm_funds_unchanged_on_accept(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.30"))
    quote_both_legs(engine, legs, participants["mm2"], Decimal("0.35"), Decimal("0.40"))

    engine.run_matching(request_id)
    engine.accept(request_id)

    mm2 = get_balance(conn, participants["mm2"])
    assert mm2["available"] == Decimal("10000")
    assert mm2["locked"] == Decimal("0")


def test_insufficient_mm_collateral_at_accept_fails(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.30"))
    engine.run_matching(request_id)

    _, _, collateral = parlay_capital(
        [(Decimal("100"), Decimal("0.40")), (Decimal("200"), Decimal("0.30"))]
    )

    conn.execute(
        "UPDATE balances SET available = %(amount)s WHERE participant_id = %(id)s",
        {"amount": collateral - Decimal("1"), "id": participants["mm1"]},
    )

    with pytest.raises(InsufficientFundsError):
        engine.accept(request_id)
