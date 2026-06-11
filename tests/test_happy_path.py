from decimal import Decimal

from rfq_engine.enums import RequestStatus
from rfq_engine.money import mm_collateral, requester_premium

from helpers import get_balance, quote_both_legs, resolve_yes, submit_two_leg_request


def test_happy_path_two_leg_accept_and_settle(engine_svc, participants):
    request_id, legs = submit_two_leg_request(engine_svc, participants["requester"])
    quote_both_legs(engine_svc, legs, participants["mm1"], Decimal("0.40"), Decimal("0.30"))

    assert engine_svc.run_matching(request_id) == RequestStatus.PRESENTED

    session = engine_svc.session
    reserved = mm_collateral(Decimal("100"), Decimal("0.40")) + mm_collateral(
        Decimal("200"), Decimal("0.30")
    )
    assert get_balance(session, participants["mm1"]).reserved == reserved

    engine_svc.accept(request_id)
    assert engine_svc.get_request_status(request_id) == RequestStatus.ESCROW_LOCKED

    resolve_yes(engine_svc, legs)
    assert engine_svc.get_request_status(request_id) == RequestStatus.SETTLED

    premium = requester_premium(Decimal("100"), Decimal("0.40")) + requester_premium(
        Decimal("200"), Decimal("0.30")
    )
    requester = get_balance(session, participants["requester"])
    mm = get_balance(session, participants["mm1"])
    assert requester.locked == Decimal("0")
    assert requester.available == Decimal("10000") - premium + Decimal("300")
    assert mm.reserved == Decimal("0")
    assert mm.locked == Decimal("0")
