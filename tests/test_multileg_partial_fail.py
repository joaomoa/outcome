from decimal import Decimal

from rfq_engine.engine import LegInput
from rfq_engine.enums import QuoteStatus, RequestStatus
from rfq_engine.queries import Queries

from helpers import get_balance, get_legs, quote_both_legs, submit_two_leg_request


def test_multileg_fails_when_one_leg_unquoted(engine, participants):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])

    engine.submit_quote(
        legs[0]["id"], participants["mm1"], Decimal("0.40"), Decimal("100"), expires_in_seconds=7200
    )

    mm_before = get_balance(engine.conn, participants["mm1"])

    assert engine.run_matching(request_id) == RequestStatus.FAILED
    assert engine.get_request_status(request_id) == RequestStatus.FAILED

    quotes = Queries(engine.conn).list_quotes_for_legs([leg["id"] for leg in legs])
    assert len(quotes) == 1
    assert quotes[0]["status"] == QuoteStatus.ACTIVE.value

    mm_after = get_balance(engine.conn, participants["mm1"])
    assert mm_after == mm_before


def test_three_leg_partial_quote_fails_atomically(engine, participants):
    request_id = engine.submit_request(
        participants["requester"],
        [
            LegInput("leg-1", Decimal("50")),
            LegInput("leg-2", Decimal("50")),
            LegInput("leg-3", Decimal("50")),
        ],
        response_deadline_seconds=3600,
    )
    legs = get_legs(engine.conn, request_id)
    for leg in [legs[0], legs[2]]:
        engine.submit_quote(
            leg["id"], participants["mm2"], Decimal("0.50"), Decimal("50"), expires_in_seconds=7200
        )
    assert engine.run_matching(request_id) == RequestStatus.FAILED


def test_parlay_matching_picks_lowest_product_price(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])

    # mm1: 0.40 * 0.30 = 0.12
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.30"))
    # mm2: 0.35 * 0.40 = 0.14 (better leg-1 alone, worse parlay)
    quote_both_legs(engine, legs, participants["mm2"], Decimal("0.35"), Decimal("0.40"))

    assert engine.run_matching(request_id) == RequestStatus.PRESENTED

    req = Queries(conn).get_request(request_id)
    assert req["parlay_price"] == Decimal("0.12")

    selected = [
        Queries(conn).get_selected_quote(leg["id"]) for leg in legs
    ]
    assert all(q["mm_id"] == participants["mm1"] for q in selected)


def test_parlay_matching_fails_when_no_mm_quotes_all_legs(engine, participants):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])

    engine.submit_quote(
        legs[0]["id"], participants["mm1"], Decimal("0.40"), Decimal("100"), expires_in_seconds=7200
    )
    engine.submit_quote(
        legs[1]["id"], participants["mm2"], Decimal("0.35"), Decimal("200"), expires_in_seconds=7200
    )

    assert engine.run_matching(request_id) == RequestStatus.FAILED
