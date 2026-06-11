from decimal import Decimal
from uuid import UUID

import psycopg

from rfq_engine.engine import LegInput, RfqEngine
from rfq_engine.enums import ResolutionOutcome


def get_legs(conn: psycopg.Connection, request_id: UUID) -> list[dict]:
    return conn.execute(
        "SELECT * FROM legs WHERE request_id = %(id)s ORDER BY leg_index",
        {"id": request_id},
    ).fetchall()


def get_balance(conn: psycopg.Connection, participant_id: UUID) -> dict:
    return conn.execute(
        "SELECT * FROM balances WHERE participant_id = %(id)s",
        {"id": participant_id},
    ).fetchone()


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


def resolve_yes(eng: RfqEngine, legs):
    eng.initiate_resolution(legs[0]["request_id"])
    for leg in legs:
        eng.resolve_leg(leg["id"], ResolutionOutcome.YES)
    eng.settle_request(legs[0]["request_id"])
