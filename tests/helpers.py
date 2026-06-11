from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from rfq_engine.engine import LegInput, RfqEngine
from rfq_engine.enums import ResolutionOutcome
from rfq_engine.models import Balance, Leg


def get_legs(session: Session, request_id) -> list[Leg]:
    return list(
        session.execute(
            select(Leg).where(Leg.request_id == request_id).order_by(Leg.leg_index)
        ).scalars().all()
    )


def get_balance(session: Session, participant_id) -> Balance:
    return session.execute(
        select(Balance).where(Balance.participant_id == participant_id)
    ).scalar_one()


def submit_two_leg_request(eng: RfqEngine, requester_id) -> tuple:
    request_id = eng.submit_request(
        requester_id=requester_id,
        legs=[
            LegInput("contract-A", Decimal("100")),
            LegInput("contract-B", Decimal("200")),
        ],
        response_deadline_seconds=3600,
    )
    legs = get_legs(eng.session, request_id)
    return request_id, legs


def quote_both_legs(eng: RfqEngine, legs, mm_id, price_a, price_b):
    eng.submit_quote(legs[0].id, mm_id, price_a, Decimal("100"), expires_in_seconds=7200)
    eng.submit_quote(legs[1].id, mm_id, price_b, Decimal("200"), expires_in_seconds=7200)


def resolve_yes(eng: RfqEngine, legs):
    eng.initiate_resolution(legs[0].request_id)
    for leg in legs:
        eng.resolve_leg(leg.id, ResolutionOutcome.YES)
    eng.settle_request(legs[0].request_id)
