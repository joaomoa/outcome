from decimal import Decimal

from rfq_engine.enums import RequestStatus

from helpers import get_balance, parlay_capital, quote_parlay, resolve_yes, submit_two_leg_request


def test_happy_path_two_leg_accept_and_settle(engine, participants):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_parlay(engine, request_id, legs, participants["mm1"], [Decimal("0.40"), Decimal("0.30")])

    assert engine.run_matching(request_id) == RequestStatus.PRESENTED

    mm_before = get_balance(engine.conn, participants["mm1"])
    assert mm_before["available"] == Decimal("10000")

    engine.accept(request_id)
    assert engine.get_request_status(request_id) == RequestStatus.ESCROW_LOCKED

    resolve_yes(engine, legs)
    assert engine.get_request_status(request_id) == RequestStatus.SETTLED

    _, premium, _ = parlay_capital(Decimal("300"), [Decimal("0.40"), Decimal("0.30")])
    requester = get_balance(engine.conn, participants["requester"])
    mm = get_balance(engine.conn, participants["mm1"])
    assert requester["locked"] == Decimal("0")
    assert requester["available"] == Decimal("10000") - premium + Decimal("300")
    assert mm["locked"] == Decimal("0")
