from decimal import Decimal

from rfq_engine.enums import RequestStatus

from helpers import get_balance, parlay_capital, quote_both_legs, resolve_yes, submit_two_leg_request


def test_happy_path_two_leg_accept_and_settle(engine, participants):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.30"))

    assert engine.run_matching(request_id) == RequestStatus.PRESENTED

    _, premium, collateral = parlay_capital(
        [(Decimal("100"), Decimal("0.40")), (Decimal("200"), Decimal("0.30"))]
    )
    assert get_balance(engine.conn, participants["mm1"])["reserved"] == collateral

    engine.accept(request_id)
    assert engine.get_request_status(request_id) == RequestStatus.ESCROW_LOCKED

    resolve_yes(engine, legs)
    assert engine.get_request_status(request_id) == RequestStatus.SETTLED

    requester = get_balance(engine.conn, participants["requester"])
    mm = get_balance(engine.conn, participants["mm1"])
    assert requester["locked"] == Decimal("0")
    assert requester["available"] == Decimal("10000") - premium + Decimal("300")
    assert mm["reserved"] == Decimal("0")
    assert mm["locked"] == Decimal("0")
