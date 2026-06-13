from datetime import timedelta
from decimal import Decimal

import pytest

from conftest import FIXED_AT
from helpers import get_balance, quote_both_legs, resolve_parlay, submit_two_leg_request
from rfq_engine.engine import DISPUTE_WINDOW_SECONDS
from rfq_engine.enums import ResolutionOutcome, ResolutionStatus
from rfq_engine.errors import DisputeWindowExpiredError, InvalidStateError
from rfq_engine.queries import Queries


def test_dispute_freezes_funds_until_arbitrator_resolves(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    req_before = get_balance(conn, participants["requester"])
    mm_before = get_balance(conn, participants["mm1"])

    engine.initiate_resolution(request_id)
    engine.report_leg_outcome(legs[0]["id"], ResolutionOutcome.YES)
    engine.report_leg_outcome(legs[1]["id"], ResolutionOutcome.NO)
    engine.propose_outcome(request_id)  # parlay NO
    engine.dispute_request(request_id)

    res = Queries(conn).get_resolution(request_id)
    assert res["status"] == ResolutionStatus.DISPUTED.value
    assert res["outcome"] == ResolutionOutcome.NO.value

    assert get_balance(conn, participants["requester"]) == req_before
    assert get_balance(conn, participants["mm1"]) == mm_before

    with pytest.raises(InvalidStateError):
        engine.settle_request(request_id)

    engine.resolve_request(request_id, ResolutionOutcome.YES)
    engine.settle_request(request_id)


def test_void_refunds_both_parties(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    resolve_parlay(
        eng=engine,
        request_id=request_id,
        legs=legs,
        component_outcomes=[ResolutionOutcome.VOID, ResolutionOutcome.YES],
    )

    req = get_balance(conn, participants["requester"])
    mm = get_balance(conn, participants["mm1"])

    assert req["available"] == Decimal("10000")
    assert req["locked"] == Decimal("0")
    assert mm["available"] == Decimal("10000")
    assert mm["locked"] == Decimal("0")


def test_cannot_dispute_while_pending(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    engine.initiate_resolution(request_id)

    with pytest.raises(InvalidStateError):
        engine.dispute_request(request_id)


def test_cannot_dispute_after_finalized(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    resolve_parlay(
        eng=engine,
        request_id=request_id,
        legs=legs,
        component_outcomes=[ResolutionOutcome.YES, ResolutionOutcome.YES],
    )

    with pytest.raises(InvalidStateError):
        engine.dispute_request(request_id)


def test_cannot_dispute_after_window_expires(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    engine.initiate_resolution(request_id)
    engine.report_leg_outcome(legs[0]["id"], ResolutionOutcome.YES)
    engine.report_leg_outcome(legs[1]["id"], ResolutionOutcome.NO)
    engine.propose_outcome(request_id)

    after_window = FIXED_AT + timedelta(seconds=DISPUTE_WINDOW_SECONDS + 1)
    with pytest.raises(DisputeWindowExpiredError):
        engine.dispute_request(request_id, at=after_window)


def test_process_resolution_expirations_auto_finalizes(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    engine.initiate_resolution(request_id)
    engine.report_leg_outcome(legs[0]["id"], ResolutionOutcome.YES)
    engine.report_leg_outcome(legs[1]["id"], ResolutionOutcome.YES)
    engine.propose_outcome(request_id)

    after_window = FIXED_AT + timedelta(seconds=DISPUTE_WINDOW_SECONDS + 1)
    finalized = engine.process_resolution_expirations(at=after_window)
    assert finalized == [request_id]

    res = Queries(conn).get_resolution(request_id)
    assert res["status"] == ResolutionStatus.RESOLVED.value
    assert res["outcome"] == ResolutionOutcome.YES.value

    engine.settle_request(request_id)

    req = get_balance(conn, participants["requester"])
    assert req["locked"] == Decimal("0")
    assert req["available"] == Decimal("10258")


def test_cannot_propose_until_all_legs_reported(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    engine.initiate_resolution(request_id)
    engine.report_leg_outcome(legs[0]["id"], ResolutionOutcome.YES)

    with pytest.raises(InvalidStateError):
        engine.propose_outcome(request_id)


def test_parlay_no_when_one_leg_loses(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    resolve_parlay(
        eng=engine,
        request_id=request_id,
        legs=legs,
        component_outcomes=[ResolutionOutcome.YES, ResolutionOutcome.NO],
    )

    req = get_balance(conn, participants["requester"])
    mm = get_balance(conn, participants["mm1"])

    assert req["locked"] == Decimal("0")
    assert mm["locked"] == Decimal("0")
    assert req["available"] == Decimal("9958")
    assert mm["available"] == Decimal("10042")
