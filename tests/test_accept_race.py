from decimal import Decimal

import pytest

from rfq_engine.enums import RequestStatus
from rfq_engine.errors import ConflictError

from helpers import quote_both_legs, submit_two_leg_request


def test_second_accept_rejected(engine_svc, participants):
    request_id, legs = submit_two_leg_request(engine_svc, participants["requester"])
    quote_both_legs(engine_svc, legs, participants["mm1"], Decimal("0.40"), Decimal("0.30"))
    engine_svc.run_matching(request_id)

    engine_svc.accept(request_id)
    assert engine_svc.get_request_status(request_id) == RequestStatus.ESCROW_LOCKED

    with pytest.raises(ConflictError):
        engine_svc.accept(request_id)
