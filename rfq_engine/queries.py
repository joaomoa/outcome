from datetime import datetime
from decimal import Decimal
from uuid import UUID

import psycopg

from rfq_engine.enums import QuoteStatus, RequestStatus, ResolutionStatus


class Queries:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def insert_participant(self, participant_id: UUID, name: str, available: Decimal) -> None:
        self.conn.execute(
            """
            WITH p AS (
                INSERT INTO participants (id, name) VALUES (%(id)s, %(name)s)
                RETURNING id
            )
            INSERT INTO balances (participant_id, available, locked)
            SELECT id, %(available)s, 0 FROM p
            """,
            {"id": participant_id, "name": name, "available": available},
        )

    def insert_request(
        self,
        request_id: UUID,
        requester_id: UUID,
        stake: Decimal,
        status: RequestStatus,
        response_deadline: datetime,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO requests (id, requester_id, stake, status, response_deadline)
            VALUES (%(id)s, %(requester_id)s, %(stake)s, %(status)s, %(response_deadline)s)
            """,
            {
                "id": request_id,
                "requester_id": requester_id,
                "stake": stake,
                "status": status.value,
                "response_deadline": response_deadline,
            },
        )

    def insert_leg(
        self,
        leg_id: UUID,
        request_id: UUID,
        contract_description: str,
        leg_index: int,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO legs (id, request_id, contract_description, leg_index)
            VALUES (%(id)s, %(request_id)s, %(desc)s, %(leg_index)s)
            """,
            {
                "id": leg_id,
                "request_id": request_id,
                "desc": contract_description,
                "leg_index": leg_index,
            },
        )

    def get_leg(self, leg_id: UUID) -> dict | None:
        return self.conn.execute(
            "SELECT * FROM legs WHERE id = %(id)s", {"id": leg_id}
        ).fetchone()

    def get_request(self, request_id: UUID, *, for_update: bool = False) -> dict | None:
        lock = " FOR UPDATE" if for_update else ""
        return self.conn.execute(
            f"SELECT * FROM requests WHERE id = %(id)s{lock}",
            {"id": request_id},
        ).fetchone()

    def get_request_status(self, request_id: UUID) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM requests WHERE id = %(id)s",
            {"id": request_id},
        ).fetchone()
        return None if row is None else row["status"]

    def update_request_status(self, request_id: UUID, status: RequestStatus) -> None:
        self.conn.execute(
            "UPDATE requests SET status = %(status)s WHERE id = %(id)s",
            {"id": request_id, "status": status.value},
        )

    def update_request_presented(
        self, request_id: UUID, accept_deadline: datetime, parlay_price: Decimal
    ) -> None:
        self.conn.execute(
            """
            UPDATE requests
            SET status = %(status)s, accept_deadline = %(accept_deadline)s,
                parlay_price = %(parlay_price)s
            WHERE id = %(id)s
            """,
            {
                "id": request_id,
                "status": RequestStatus.PRESENTED.value,
                "accept_deadline": accept_deadline,
                "parlay_price": parlay_price,
            },
        )

    def list_legs(self, request_id: UUID) -> list[dict]:
        return self.conn.execute(
            "SELECT * FROM legs WHERE request_id = %(id)s ORDER BY leg_index",
            {"id": request_id},
        ).fetchall()

    def insert_quote(
        self,
        quote_id: UUID,
        leg_id: UUID,
        mm_id: UUID,
        price: Decimal,
        expires_at: datetime,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO quotes (id, leg_id, mm_id, price, expires_at, status)
            VALUES (%(id)s, %(leg_id)s, %(mm_id)s, %(price)s, %(expires_at)s, %(status)s)
            """,
            {
                "id": quote_id,
                "leg_id": leg_id,
                "mm_id": mm_id,
                "price": price,
                "expires_at": expires_at,
                "status": QuoteStatus.ACTIVE.value,
            },
        )

    def upsert_parlay_quote(
        self,
        request_id: UUID,
        mm_id: UUID,
        size: Decimal,
        expires_at: datetime,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO parlay_quotes (request_id, mm_id, size, expires_at, status)
            VALUES (%(request_id)s, %(mm_id)s, %(size)s, %(expires_at)s, %(status)s)
            ON CONFLICT (request_id, mm_id) DO UPDATE SET
                size = EXCLUDED.size,
                expires_at = EXCLUDED.expires_at,
                status = EXCLUDED.status
            """,
            {
                "request_id": request_id,
                "mm_id": mm_id,
                "size": size,
                "expires_at": expires_at,
                "status": QuoteStatus.ACTIVE.value,
            },
        )

    def get_parlay_quote(self, request_id: UUID, mm_id: UUID) -> dict | None:
        return self.conn.execute(
            """
            SELECT * FROM parlay_quotes
            WHERE request_id = %(request_id)s AND mm_id = %(mm_id)s
            """,
            {"request_id": request_id, "mm_id": mm_id},
        ).fetchone()

    def update_quote_status(self, quote_id: UUID, status: QuoteStatus) -> None:
        self.conn.execute(
            "UPDATE quotes SET status = %(status)s WHERE id = %(id)s",
            {"id": quote_id, "status": status.value},
        )

    def update_parlay_quote_status(
        self, request_id: UUID, mm_id: UUID, status: QuoteStatus
    ) -> None:
        self.conn.execute(
            """
            UPDATE parlay_quotes SET status = %(status)s
            WHERE request_id = %(request_id)s AND mm_id = %(mm_id)s
            """,
            {
                "request_id": request_id,
                "mm_id": mm_id,
                "status": status.value,
            },
        )

    def get_selected_quote(self, leg_id: UUID) -> dict | None:
        return self.conn.execute(
            """
            SELECT * FROM quotes
            WHERE leg_id = %(leg_id)s AND status = %(status)s
            """,
            {"leg_id": leg_id, "status": QuoteStatus.SELECTED.value},
        ).fetchone()

    def get_selected_parlay_quote(self, request_id: UUID) -> dict | None:
        return self.conn.execute(
            """
            SELECT * FROM parlay_quotes
            WHERE request_id = %(request_id)s AND status = %(status)s
            """,
            {"request_id": request_id, "status": QuoteStatus.SELECTED.value},
        ).fetchone()

    def list_quotes_for_leg(
        self,
        leg_id: UUID | list[UUID],
        *,
        status: QuoteStatus | None = None,
    ) -> list[dict]:
        leg_ids = [leg_id] if isinstance(leg_id, UUID) else leg_id
        if status is None:
            return self.conn.execute(
                "SELECT * FROM quotes WHERE leg_id = ANY(%(ids)s)",
                {"ids": leg_ids},
            ).fetchall()
        return self.conn.execute(
            """
            SELECT * FROM quotes
            WHERE leg_id = ANY(%(ids)s) AND status = %(status)s
            """,
            {"ids": leg_ids, "status": status.value},
        ).fetchall()

    def list_parlay_quotes_for_request(
        self,
        request_id: UUID,
        *,
        status: QuoteStatus | None = None,
    ) -> list[dict]:
        if status is None:
            return self.conn.execute(
                "SELECT * FROM parlay_quotes WHERE request_id = %(request_id)s",
                {"request_id": request_id},
            ).fetchall()
        return self.conn.execute(
            """
            SELECT * FROM parlay_quotes
            WHERE request_id = %(request_id)s AND status = %(status)s
            """,
            {"request_id": request_id, "status": status.value},
        ).fetchall()

    def insert_escrow(
        self,
        escrow_id: UUID,
        request_id: UUID,
        requester_id: UUID,
        mm_id: UUID,
        requester_locked: Decimal,
        mm_locked: Decimal,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO escrows (id, request_id, requester_id, mm_id, requester_locked, mm_locked)
            VALUES (%(id)s, %(request_id)s, %(requester_id)s, %(mm_id)s, %(req)s, %(mm)s)
            """,
            {
                "id": escrow_id,
                "request_id": request_id,
                "requester_id": requester_id,
                "mm_id": mm_id,
                "req": requester_locked,
                "mm": mm_locked,
            },
        )

    def list_expired_presented_requests(self, at: datetime) -> list[dict]:
        return self.conn.execute(
            """
            SELECT id FROM requests
            WHERE status = %(status)s AND accept_deadline < %(at)s
            FOR UPDATE
            """,
            {"status": RequestStatus.PRESENTED.value, "at": at},
        ).fetchall()

    def insert_resolution(self, resolution_id: UUID, request_id: UUID) -> None:
        self.conn.execute(
            """
            INSERT INTO resolutions (id, request_id, status)
            VALUES (%(id)s, %(request_id)s, %(status)s)
            """,
            {
                "id": resolution_id,
                "request_id": request_id,
                "status": ResolutionStatus.PENDING.value,
            },
        )

    def get_resolution(self, request_id: UUID, *, for_update: bool = False) -> dict | None:
        lock = " FOR UPDATE" if for_update else ""
        return self.conn.execute(
            f"SELECT * FROM resolutions WHERE request_id = %(request_id)s{lock}",
            {"request_id": request_id},
        ).fetchone()

    def update_resolution(
        self,
        request_id: UUID,
        status: ResolutionStatus,
        *,
        outcome: str | None = None,
    ) -> None:
        if outcome is None:
            self.conn.execute(
                """
                UPDATE resolutions SET status = %(status)s
                WHERE request_id = %(request_id)s
                """,
                {"request_id": request_id, "status": status.value},
            )
            return
        self.conn.execute(
            """
            UPDATE resolutions SET status = %(status)s, outcome = %(outcome)s
            WHERE request_id = %(request_id)s
            """,
            {"request_id": request_id, "status": status.value, "outcome": outcome},
        )

    def propose_resolution(
        self,
        request_id: UUID,
        outcome: str,
        dispute_deadline: datetime,
    ) -> None:
        self.conn.execute(
            """
            UPDATE resolutions
            SET status = %(status)s, outcome = %(outcome)s, dispute_deadline = %(deadline)s
            WHERE request_id = %(request_id)s
            """,
            {
                "request_id": request_id,
                "status": ResolutionStatus.PROPOSED.value,
                "outcome": outcome,
                "deadline": dispute_deadline,
            },
        )

    def list_expired_proposed_resolutions(self, at: datetime) -> list[dict]:
        return self.conn.execute(
            """
            SELECT request_id, outcome FROM resolutions
            WHERE status = %(status)s AND dispute_deadline < %(at)s
            FOR UPDATE
            """,
            {"status": ResolutionStatus.PROPOSED.value, "at": at},
        ).fetchall()

    def set_leg_component_outcome(self, leg_id: UUID, outcome: str) -> None:
        self.conn.execute(
            """
            UPDATE legs SET component_outcome = %(outcome)s
            WHERE id = %(leg_id)s
            """,
            {"leg_id": leg_id, "outcome": outcome},
        )

    def list_escrows_for_request(self, request_id: UUID) -> list[dict]:
        return self.conn.execute(
            "SELECT * FROM escrows WHERE request_id = %(request_id)s",
            {"request_id": request_id},
        ).fetchall()

    def get_balance(self, participant_id: UUID) -> dict | None:
        return self.conn.execute(
            "SELECT * FROM balances WHERE participant_id = %(id)s",
            {"id": participant_id},
        ).fetchone()
