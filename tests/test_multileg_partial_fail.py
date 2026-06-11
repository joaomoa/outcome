from decimal import Decimal

from sqlalchemy import select

from rfq_engine.engine import LegInput
from rfq_engine.enums import QuoteStatus, RequestStatus
from rfq_engine.models import Quote
from rfq_engine.money import mm_collateral

from helpers import get_balance, get_legs, submit_two_leg_request


def test_multileg_fails_when_one_leg_unquoted(engine_svc, participants):
    session = engine_svc.session
    request_id, legs = submit_two_leg_request(engine_svc, participants["requester"])

    engine_svc.submit_quote(
        legs[0].id, participants["mm1"], Decimal("0.40"), Decimal("100"), expires_in_seconds=7200
    )

    mm_before = get_balance(session, participants["mm1"])
    expected_reserved = mm_collateral(Decimal("100"), Decimal("0.40"))

    assert engine_svc.run_matching(request_id) == RequestStatus.FAILED
    assert engine_svc.get_request_status(request_id) == RequestStatus.FAILED

    leg_ids = [leg.id for leg in legs]
    quotes = session.execute(select(Quote).where(Quote.leg_id.in_(leg_ids))).scalars().all()
    assert all(q.status == QuoteStatus.ACTIVE.value for q in quotes)
    assert not session.execute(
        select(Quote)
        .where(Quote.leg_id.in_(leg_ids))
        .where(Quote.status == QuoteStatus.SELECTED.value)
    ).scalars().all()

    mm_after = get_balance(session, participants["mm1"])
    assert mm_after.reserved == expected_reserved
    assert mm_after.available == mm_before.available


def test_three_leg_partial_quote_fails_atomically(engine_svc, participants):
    request_id = engine_svc.submit_request(
        participants["requester"],
        [
            LegInput("leg-1", Decimal("50")),
            LegInput("leg-2", Decimal("50")),
            LegInput("leg-3", Decimal("50")),
        ],
        response_deadline_seconds=3600,
    )
    legs = get_legs(engine_svc.session, request_id)
    for leg in [legs[0], legs[2]]:
        engine_svc.submit_quote(
            leg.id, participants["mm2"], Decimal("0.50"), Decimal("50"), expires_in_seconds=7200
        )
    assert engine_svc.run_matching(request_id) == RequestStatus.FAILED
