from datetime import timedelta
from decimal import Decimal

import pytest

from conftest import FIXED_AT
from helpers import get_balance, quote_parlay, submit_two_leg_request
from rfq_engine.engine import ACCEPT_WINDOW_SECONDS
from rfq_engine.enums import QuoteStatus, RequestStatus
from rfq_engine.errors import QuoteExpiredError
from rfq_engine.queries import Queries


def test_accept_rejected_when_quote_expires(engine, participants):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])

    engine.submit_quote(
        legs[0]["id"], participants["mm1"], Decimal("0.40"), expires_in_seconds=60
    )
    engine.submit_quote(
        legs[1]["id"], participants["mm1"], Decimal("0.30"), expires_in_seconds=7200
    )
    engine.submit_parlay_quote(request_id, participants["mm1"], Decimal("300"), expires_in_seconds=7200)

    engine.run_matching(request_id)
    assert get_balance(engine.conn, participants["mm1"])["available"] == Decimal("10000")

    with pytest.raises(QuoteExpiredError):
        engine.accept(request_id, at=FIXED_AT + timedelta(seconds=120))

    assert engine.get_request_status(request_id) == RequestStatus.FAILED
    mm = get_balance(engine.conn, participants["mm1"])
    assert mm["available"] == Decimal("10000")
    assert mm["locked"] == Decimal("0")


def test_reject_presented_request(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_parlay(engine, request_id, legs, participants["mm1"], [Decimal("0.40"), Decimal("0.30")])
    engine.run_matching(request_id)

    engine.reject(request_id)

    assert engine.get_request_status(request_id) == RequestStatus.REJECTED
    quotes = Queries(conn).list_quotes_for_leg([leg["id"] for leg in legs])
    assert all(q["status"] == QuoteStatus.REJECTED.value for q in quotes)

    mm = get_balance(conn, participants["mm1"])
    assert mm["available"] == Decimal("10000")
    assert mm["locked"] == Decimal("0")


def test_process_expirations_marks_accept_window_expired(engine, participants, conn):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_parlay(engine, request_id, legs, participants["mm1"], [Decimal("0.40"), Decimal("0.30")])
    engine.run_matching(request_id)

    after_window = FIXED_AT + timedelta(seconds=ACCEPT_WINDOW_SECONDS + 1)
    expired = engine.process_expirations(at=after_window)
    assert expired == [request_id]

    assert engine.get_request_status(request_id) == RequestStatus.EXPIRED
    quotes = Queries(conn).list_quotes_for_leg([leg["id"] for leg in legs])
    assert all(q["status"] == QuoteStatus.EXPIRED.value for q in quotes)

    mm = get_balance(conn, participants["mm1"])
    assert mm["available"] == Decimal("10000")
    assert mm["locked"] == Decimal("0")
