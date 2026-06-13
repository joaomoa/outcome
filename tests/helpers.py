from decimal import Decimal
from uuid import UUID

import psycopg

from rfq_engine.engine import LegInput, RfqEngine
from rfq_engine.enums import ResolutionOutcome
from rfq_engine.queries import Queries


def parlay_capital(notional_and_prices: list[tuple[Decimal, Decimal]]) -> tuple[Decimal, Decimal, Decimal]:
    total = sum(n for n, _ in notional_and_prices)
    price = Decimal("1")
    for _, p in notional_and_prices:
        price *= p
    premium = total * price
    collateral = total * (Decimal("1") - price)
    return price, premium, collateral


def get_legs(conn: psycopg.Connection, request_id: UUID) -> list[dict]:
    return Queries(conn).list_legs(request_id)


def get_balance(conn: psycopg.Connection, participant_id: UUID) -> dict:
    return Queries(conn).get_balance(participant_id)


def submit_two_leg_request(eng: RfqEngine, requester_id: UUID) -> tuple:
    request_id = eng.submit_request(
        requester_id=requester_id,
        legs=[
            LegInput("contract-A", Decimal("100")),
            LegInput("contract-B", Decimal("200")),
        ],
        response_deadline_seconds=3600,
    )
    legs = get_legs(eng.conn, request_id)
    return request_id, legs


def quote_both_legs(eng: RfqEngine, legs, mm_id, price_a, price_b):
    eng.submit_quote(legs[0]["id"], mm_id, price_a, Decimal("100"), expires_in_seconds=7200)
    eng.submit_quote(legs[1]["id"], mm_id, price_b, Decimal("200"), expires_in_seconds=7200)


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
