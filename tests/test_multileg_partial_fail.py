from decimal import Decimal

from rfq_engine.engine import LegInput
from rfq_engine.enums import QuoteStatus, RequestStatus

from helpers import get_balance, get_legs, submit_two_leg_request


def test_multileg_fails_when_one_leg_unquoted(engine_svc, participants):
    request_id, legs = submit_two_leg_request(engine_svc, participants["requester"])

    engine_svc.submit_quote(
        legs[0]["id"], participants["mm1"], Decimal("0.40"), Decimal("100"), expires_in_seconds=7200
    )

    mm_before = get_balance(engine_svc.conn, participants["mm1"])
    expected_reserved = Decimal("100") * Decimal("0.60")

    assert engine_svc.run_matching(request_id) == RequestStatus.FAILED
    assert engine_svc.get_request_status(request_id) == RequestStatus.FAILED

    leg_ids = [leg["id"] for leg in legs]
    quotes = engine_svc.conn.execute(
        "SELECT * FROM quotes WHERE leg_id = ANY(%(ids)s)",
        {"ids": leg_ids},
    ).fetchall()
    assert all(q["status"] == QuoteStatus.ACTIVE.value for q in quotes)
    selected = engine_svc.conn.execute(
        "SELECT * FROM quotes WHERE leg_id = ANY(%(ids)s) AND status = %(status)s",
        {"ids": leg_ids, "status": QuoteStatus.SELECTED.value},
    ).fetchall()
    assert not selected

    mm_after = get_balance(engine_svc.conn, participants["mm1"])
    assert mm_after["reserved"] == expected_reserved
    assert mm_after["available"] == mm_before["available"]


def test_three_leg_partial_quote_fails_atomically(engine_svc, participants):
    request_id = engine_svc.submit_request(
        participants["requester"],
        [
            LegInput("leg-1", Decimal("50")),
            LegInput("leg-2", Decimal("50")),
            LegInput("leg-3", Decimal("50")),
        ],
        response_deadline_seconds=3600,
    )
    legs = get_legs(engine_svc.conn, request_id)
    for leg in [legs[0], legs[2]]:
        engine_svc.submit_quote(
            leg["id"], participants["mm2"], Decimal("0.50"), Decimal("50"), expires_in_seconds=7200
        )
    assert engine_svc.run_matching(request_id) == RequestStatus.FAILED
