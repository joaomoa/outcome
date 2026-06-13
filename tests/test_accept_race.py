from decimal import Decimal

import pytest

from rfq_engine.enums import RequestStatus
from rfq_engine.errors import ConflictError

from helpers import quote_parlay, submit_two_leg_request


def test_second_accept_rejected(engine, participants):
    request_id, legs = submit_two_leg_request(engine, participants["requester"])
    quote_parlay(engine, request_id, legs, participants["mm1"], [Decimal("0.40"), Decimal("0.30")])
    engine.run_matching(request_id)

    engine.accept(request_id)
    assert engine.get_request_status(request_id) == RequestStatus.ESCROW_LOCKED

    with pytest.raises(ConflictError):
        engine.accept(request_id)
