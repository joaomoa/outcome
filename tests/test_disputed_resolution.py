from decimal import Decimal

from datetime import datetime, timedelta, timezone

import pytest

from helpers import get_balance, quote_both_legs, submit_two_leg_request
from rfq_engine.engine import DISPUTE_WINDOW_SECONDS
from rfq_engine.enums import ResolutionOutcome, ResolutionStatus
from rfq_engine.errors import DisputeWindowExpiredError, InvalidStateError
from rfq_engine.queries import Queries

from conftest import FIXED_AT


def test_dispute_freezes_funds_until_arbitrator_resolves(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    req_before = get_balance(conn, participants["requester"])
    mm_before = get_balance(conn, participants["mm1"])

    engine.initiate_resolution(request_id)
    engine.propose_outcome(legs[0]["id"], ResolutionOutcome.NO)
    engine.dispute_leg(legs[0]["id"])

    res = Queries(conn).get_resolution(legs[0]["id"])
    assert res["status"] == ResolutionStatus.DISPUTED.value

    assert get_balance(conn, participants["requester"]) == req_before
    assert get_balance(conn, participants["mm1"]) == mm_before

    with pytest.raises(InvalidStateError):
        engine.settle_request(request_id)

    engine.resolve_leg(legs[0]["id"], ResolutionOutcome.YES)
    engine.propose_outcome(legs[1]["id"], ResolutionOutcome.NO)
    engine.finalize_leg(legs[1]["id"])
    engine.settle_request(request_id)


def test_void_refunds_both_parties(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    engine.initiate_resolution(request_id)
    engine.propose_outcome(legs[0]["id"], ResolutionOutcome.VOID)
    engine.finalize_leg(legs[0]["id"])
    engine.propose_outcome(legs[1]["id"], ResolutionOutcome.YES)
    engine.finalize_leg(legs[1]["id"])
    engine.settle_request(request_id)

    req = get_balance(conn, participants["requester"])
    mm = get_balance(conn, participants["mm1"])

    assert req["available"] == Decimal("10130")
    assert req["locked"] == Decimal("0")
    assert mm["available"] == Decimal("9870")
    assert mm["locked"] == Decimal("0")


def test_cannot_dispute_while_pending(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    engine.initiate_resolution(request_id)

    with pytest.raises(InvalidStateError):
        engine.dispute_leg(legs[0]["id"])


def test_cannot_dispute_after_finalized(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    engine.initiate_resolution(request_id)
    engine.propose_outcome(legs[0]["id"], ResolutionOutcome.YES)
    engine.finalize_leg(legs[0]["id"])

    with pytest.raises(InvalidStateError):
        engine.dispute_leg(legs[0]["id"])


def test_cannot_dispute_after_window_expires(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    engine.initiate_resolution(request_id)
    engine.propose_outcome(legs[0]["id"], ResolutionOutcome.NO)

    after_window = FIXED_AT + timedelta(seconds=DISPUTE_WINDOW_SECONDS + 1)
    with pytest.raises(DisputeWindowExpiredError):
        engine.dispute_leg(legs[0]["id"], at=after_window)


def test_process_resolution_expirations_auto_finalizes(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_both_legs(engine, legs, participants["mm1"], Decimal("0.40"), Decimal("0.35"))
    engine.run_matching(request_id)
    engine.accept(request_id)

    engine.initiate_resolution(request_id)
    engine.propose_outcome(legs[0]["id"], ResolutionOutcome.YES)
    engine.propose_outcome(legs[1]["id"], ResolutionOutcome.YES)

    after_window = FIXED_AT + timedelta(seconds=DISPUTE_WINDOW_SECONDS + 1)
    finalized = engine.process_resolution_expirations(at=after_window)
    assert set(finalized) == {legs[0]["id"], legs[1]["id"]}

    res0 = Queries(conn).get_resolution(legs[0]["id"])
    assert res0["status"] == ResolutionStatus.RESOLVED.value

    engine.settle_request(request_id)

    req = get_balance(conn, participants["requester"])
    assert req["locked"] == Decimal("0")
    assert req["available"] == Decimal("10190")
