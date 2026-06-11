from datetime import timedelta
from decimal import Decimal

import pytest

from rfq_engine.engine import RfqEngine
from rfq_engine.enums import RequestStatus
from rfq_engine.errors import QuoteExpiredError

from conftest import FIXED_AT
from helpers import get_balance, quote_both_legs, submit_two_leg_request


def test_accept_rejected_when_quote_expires(conn, participants):
    eng = RfqEngine(conn, FIXED_AT)
    request_id, legs = submit_two_leg_request(eng, participants["requester"])

    eng.submit_quote(
        legs[0]["id"], participants["mm1"], Decimal("0.40"), Decimal("100"), expires_in_seconds=60
    )
    eng.submit_quote(
        legs[1]["id"], participants["mm1"], Decimal("0.30"), Decimal("200"), expires_in_seconds=7200
    )

    eng.run_matching(request_id)
    reserved = Decimal("100") * Decimal("0.60") + Decimal("200") * Decimal("0.70")
    assert get_balance(conn, participants["mm1"])["reserved"] == reserved

    late = RfqEngine(conn, FIXED_AT + timedelta(seconds=120))
    with pytest.raises(QuoteExpiredError):
        late.accept(request_id)

    assert eng.get_request_status(request_id) == RequestStatus.FAILED
    mm = get_balance(conn, participants["mm1"])
    assert mm["reserved"] == Decimal("0")
    assert mm["available"] == Decimal("10000")
