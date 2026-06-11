from decimal import Decimal

from rfq_engine.enums import RequestStatus

from helpers import get_balance, quote_both_legs, resolve_yes, submit_two_leg_request


def test_happy_path_two_leg_accept_and_settle(engine_svc, participants):
    request_id, legs = submit_two_leg_request(engine_svc, participants["requester"])
    quote_both_legs(engine_svc, legs, participants["mm1"], Decimal("0.40"), Decimal("0.30"))

    assert engine_svc.run_matching(request_id) == RequestStatus.PRESENTED

    reserved = Decimal("100") * Decimal("0.60") + Decimal("200") * Decimal("0.70")
    assert get_balance(engine_svc.conn, participants["mm1"])["reserved"] == reserved

    engine_svc.accept(request_id)
    assert engine_svc.get_request_status(request_id) == RequestStatus.ESCROW_LOCKED

    resolve_yes(engine_svc, legs)
    assert engine_svc.get_request_status(request_id) == RequestStatus.SETTLED

    premium = Decimal("100") * Decimal("0.40") + Decimal("200") * Decimal("0.30")
    requester = get_balance(engine_svc.conn, participants["requester"])
    mm = get_balance(engine_svc.conn, participants["mm1"])
    assert requester["locked"] == Decimal("0")
    assert requester["available"] == Decimal("10000") - premium + Decimal("300")
    assert mm["reserved"] == Decimal("0")
    assert mm["locked"] == Decimal("0")
