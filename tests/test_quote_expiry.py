from datetime import timedelta
from decimal import Decimal

import pytest

from rfq_engine.enums import RequestStatus
from rfq_engine.errors import QuoteExpiredError

from conftest import FIXED_AT
from helpers import get_balance, parlay_capital, submit_two_leg_request


def test_accept_rejected_when_quote_expires(engine, participants):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])

    engine.submit_quote(
        legs[0]["id"], participants["mm1"], Decimal("0.40"), Decimal("100"), expires_in_seconds=60
    )
    engine.submit_quote(
        legs[1]["id"], participants["mm1"], Decimal("0.30"), Decimal("200"), expires_in_seconds=7200
    )

    engine.run_matching(request_id)
    _, _, collateral = parlay_capital(
        [(Decimal("100"), Decimal("0.40")), (Decimal("200"), Decimal("0.30"))]
    )
    assert get_balance(engine.conn, participants["mm1"])["reserved"] == collateral

    with pytest.raises(QuoteExpiredError):
        engine.accept(request_id, at=FIXED_AT + timedelta(seconds=120))

    assert engine.get_request_status(request_id) == RequestStatus.FAILED
    mm = get_balance(engine.conn, participants["mm1"])
    assert mm["reserved"] == Decimal("0")
    assert mm["available"] == Decimal("10000")
