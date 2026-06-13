from decimal import Decimal
from uuid import UUID

import psycopg

from rfq_engine.engine import RfqEngine
from rfq_engine.enums import ResolutionOutcome
from rfq_engine.queries import Queries

DEFAULT_STAKE = Decimal("300")


def parlay_capital(stake: Decimal, prices: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    price = Decimal("1")
    for p in prices:
        price *= p
    premium = stake * price
    collateral = stake * (Decimal("1") - price)
    return price, premium, collateral


def get_legs(conn: psycopg.Connection, request_id: UUID) -> list[dict]:
    return Queries(conn).list_legs(request_id)


def get_balance(conn: psycopg.Connection, participant_id: UUID) -> dict:
    return Queries(conn).get_balance(participant_id)


def submit_two_leg_request(eng: RfqEngine, requester_id: UUID, stake: Decimal = DEFAULT_STAKE) -> tuple:
    request_id = eng.submit_request(
        requester_id=requester_id,
        stake=stake,
        legs=["contract-A", "contract-B"],
        response_deadline_seconds=3600,
    )
    legs = get_legs(eng.conn, request_id)
    return request_id, legs


def quote_parlay(
    eng: RfqEngine,
    request_id: UUID,
    legs: list[dict],
    mm_id: UUID,
    prices: list[Decimal],
    *,
    size: Decimal = DEFAULT_STAKE,
    expires_in_seconds: float = 7200,
):
    for leg, price in zip(legs, prices):
        eng.submit_quote(leg["id"], mm_id, price, expires_in_seconds=expires_in_seconds)
    eng.submit_parlay_quote(request_id, mm_id, size, expires_in_seconds=expires_in_seconds)


def resolve_parlay(eng: RfqEngine, request_id: UUID, legs, component_outcomes: list[ResolutionOutcome]):
    eng.initiate_resolution(request_id)
    for leg, outcome in zip(legs, component_outcomes):
        eng.report_leg_outcome(leg["id"], outcome)
    eng.propose_outcome(request_id)
    eng.finalize_request(request_id)
    eng.settle_request(request_id)


def resolve_yes(eng: RfqEngine, legs):
    resolve_parlay(
        eng,
        legs[0]["request_id"],
        legs,
        [ResolutionOutcome.YES] * len(legs),
    )
