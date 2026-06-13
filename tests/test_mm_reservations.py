from decimal import Decimal

from helpers import get_balance, parlay_capital, quote_both_legs, submit_two_leg_request
from rfq_engine.enums import QuoteStatus
from rfq_engine.queries import Queries


def test_mm_reserve_reconciles_to_parlay_collateral(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    mm_before = get_balance(conn, participants["mm1"])

    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))

    _, premium, collateral = parlay_capital(
        [(Decimal("100"), Decimal("0.40")), (Decimal("200"), Decimal("0.35"))]
    )
    mm_after = get_balance(conn, participants["mm1"])

    assert mm_after["available"] == mm_before["available"] - collateral
    assert mm_after["reserved"] == collateral
    assert premium == Decimal("300") * Decimal("0.40") * Decimal("0.35")

    quotes = Queries(conn).list_quotes_for_legs([leg["id"] for leg in legs])
    assert sum(q["reserved_amount"] for q in quotes) == collateral


def test_winning_mm_reserved_moves_to_locked_on_accept(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.30"))
    engine.run_matching(request_id)

    _, premium, collateral = parlay_capital(
        [(Decimal("100"), Decimal("0.40")), (Decimal("200"), Decimal("0.30"))]
    )
    assert get_balance(conn, participants["mm1"])["reserved"] == collateral

    engine.accept(request_id)

    mm = get_balance(conn, participants["mm1"])
    assert mm["reserved"] == Decimal("0")
    assert mm["locked"] == collateral
    assert mm["available"] == Decimal("10000") - collateral

    req = get_balance(conn, participants["requester"])
    assert req["locked"] == premium


def test_losing_mm_reservations_released_on_accept(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])

    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.30"))
    quote_both_legs(engine, legs, participants["mm2"], Decimal("0.35"), Decimal("0.40"))

    _, _, mm2_collateral = parlay_capital(
        [(Decimal("100"), Decimal("0.35")), (Decimal("200"), Decimal("0.40"))]
    )

    engine.run_matching(request_id)
    assert get_balance(conn, participants["mm2"])["reserved"] == mm2_collateral

    engine.accept(request_id)

    mm2 = get_balance(conn, participants["mm2"])
    assert mm2["reserved"] == Decimal("0")
    assert mm2["locked"] == Decimal("0")
    assert mm2["available"] == Decimal("10000")

    quotes = Queries(conn).list_quotes_for_legs([leg["id"] for leg in legs])
    mm2_quotes = [q for q in quotes if q["mm_id"] == participants["mm2"]]
    assert all(q["status"] == QuoteStatus.REJECTED.value for q in mm2_quotes)


def test_reject_releases_all_mm_reservations(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.30"))
    engine.run_matching(request_id)

    _, _, collateral = parlay_capital(
        [(Decimal("100"), Decimal("0.40")), (Decimal("200"), Decimal("0.30"))]
    )
    assert get_balance(conn, participants["mm1"])["reserved"] == collateral

    engine.reject(request_id)

    mm = get_balance(conn, participants["mm1"])
    assert mm["reserved"] == Decimal("0")
    assert mm["available"] == Decimal("10000")
