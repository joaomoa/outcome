import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import psycopg

from rfq_engine.enums import QuoteStatus, RequestStatus, ResolutionOutcome, ResolutionStatus
from rfq_engine.errors import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    QuoteExpiredError,
)
from rfq_engine.ledger import Ledger

ACCEPT_WINDOW_SECONDS = 300.0


@dataclass
class LegInput:
    contract_description: str
    notional: Decimal


class RfqEngine:
    def __init__(self, conn: psycopg.Connection, at: datetime) -> None:
        self.conn = conn
        self.at = at
        self._ledger = Ledger(conn)

    def create_participant(self, name: str, initial_balance: Decimal) -> UUID:
        participant_id = uuid.uuid4()
        self.conn.execute(
            "INSERT INTO participants (id, name) VALUES (%(id)s, %(name)s)",
            {"id": participant_id, "name": name},
        )
        self.conn.execute(
            """
            INSERT INTO balances (participant_id, available, reserved, locked)
            VALUES (%(id)s, %(available)s, 0, 0)
            """,
            {"id": participant_id, "available": initial_balance},
        )
        return participant_id

    def submit_request(
        self,
        requester_id: UUID,
        legs: list[LegInput],
        response_deadline_seconds: float,
    ) -> UUID:
        request_id = uuid.uuid4()
        self.conn.execute(
            """
            INSERT INTO requests (id, requester_id, status, response_deadline)
            VALUES (%(id)s, %(requester_id)s, %(status)s, %(response_deadline)s)
            """,
            {
                "id": request_id,
                "requester_id": requester_id,
                "status": RequestStatus.QUOTING.value,
                "response_deadline": self.at + timedelta(seconds=response_deadline_seconds),
            },
        )
        for i, leg in enumerate(legs):
            self.conn.execute(
                """
                INSERT INTO legs (id, request_id, contract_description, notional, leg_index)
                VALUES (%(id)s, %(request_id)s, %(desc)s, %(notional)s, %(leg_index)s)
                """,
                {
                    "id": uuid.uuid4(),
                    "request_id": request_id,
                    "desc": leg.contract_description,
                    "notional": leg.notional,
                    "leg_index": i,
                },
            )
        return request_id

    def submit_quote(
        self,
        leg_id: UUID,
        mm_id: UUID,
        price: Decimal,
        size: Decimal,
        expires_in_seconds: float,
    ) -> UUID:
        leg = self.conn.execute(
            "SELECT * FROM legs WHERE id = %(id)s", {"id": leg_id}
        ).fetchone()
        if leg is None:
            raise NotFoundError(f"leg {leg_id} not found")

        req = self.conn.execute(
            "SELECT * FROM requests WHERE id = %(id)s FOR UPDATE",
            {"id": leg["request_id"]},
        ).fetchone()

        if req["status"] not in (RequestStatus.OPEN.value, RequestStatus.QUOTING.value):
            raise InvalidStateError(f"cannot quote in status {req['status']}")
        if self.at >= req["response_deadline"]:
            raise InvalidStateError("response deadline passed")
        if size < leg["notional"]:
            raise InvalidStateError(f"size {size} < notional {leg['notional']}")

        reserve = leg["notional"] * (Decimal("1") - price)
        quote_id = uuid.uuid4()
        self.conn.execute(
            """
            INSERT INTO quotes (id, leg_id, mm_id, price, size, expires_at, status, reserved_amount)
            VALUES (%(id)s, %(leg_id)s, %(mm_id)s, %(price)s, %(size)s, %(expires_at)s, %(status)s, %(reserved)s)
            """,
            {
                "id": quote_id,
                "leg_id": leg_id,
                "mm_id": mm_id,
                "price": price,
                "size": size,
                "expires_at": self.at + timedelta(seconds=expires_in_seconds),
                "status": QuoteStatus.ACTIVE.value,
                "reserved": reserve,
            },
        )
        self._ledger.reserve(mm_id, reserve)
        return quote_id

    def run_matching(self, request_id: UUID) -> RequestStatus:
        req = self.conn.execute(
            "SELECT * FROM requests WHERE id = %(id)s FOR UPDATE",
            {"id": request_id},
        ).fetchone()
        if req is None:
            raise NotFoundError(f"request {request_id} not found")
        if req["status"] not in (RequestStatus.QUOTING.value, RequestStatus.OPEN.value):
            raise InvalidStateError(f"cannot match in status {req['status']}")

        legs = self.conn.execute(
            "SELECT * FROM legs WHERE request_id = %(id)s ORDER BY leg_index",
            {"id": request_id},
        ).fetchall()

        selected: list[dict] = []
        for leg in legs:
            best = self._best_quote(leg["id"], leg["notional"])
            if best is None:
                self.conn.execute(
                    "UPDATE requests SET status = %(status)s WHERE id = %(id)s",
                    {"id": request_id, "status": RequestStatus.FAILED.value},
                )
                return RequestStatus.FAILED
            selected.append(best)

        for quote in selected:
            self.conn.execute(
                "UPDATE quotes SET status = %(status)s WHERE id = %(id)s",
                {"id": quote["id"], "status": QuoteStatus.SELECTED.value},
            )
        self.conn.execute(
            """
            UPDATE requests
            SET status = %(status)s, accept_deadline = %(accept_deadline)s
            WHERE id = %(id)s
            """,
            {
                "id": request_id,
                "status": RequestStatus.PRESENTED.value,
                "accept_deadline": self.at + timedelta(seconds=ACCEPT_WINDOW_SECONDS),
            },
        )
        return RequestStatus.PRESENTED

    def accept(self, request_id: UUID) -> None:
        req = self.conn.execute(
            "SELECT * FROM requests WHERE id = %(id)s FOR UPDATE",
            {"id": request_id},
        ).fetchone()
        if req is None:
            raise NotFoundError(f"request {request_id} not found")
        if req["status"] != RequestStatus.PRESENTED.value:
            raise ConflictError(f"cannot accept in status {req['status']}")
        if req["accept_deadline"] is None or self.at > req["accept_deadline"]:
            raise QuoteExpiredError("accept window expired")

        legs = self.conn.execute(
            "SELECT * FROM legs WHERE request_id = %(id)s ORDER BY leg_index",
            {"id": request_id},
        ).fetchall()

        selected: list[tuple[dict, dict]] = []
        for leg in legs:
            quote = self.conn.execute(
                """
                SELECT * FROM quotes
                WHERE leg_id = %(leg_id)s AND status = %(status)s
                """,
                {"leg_id": leg["id"], "status": QuoteStatus.SELECTED.value},
            ).fetchone()
            if quote is None:
                raise InvalidStateError(f"leg {leg['id']} has no selected quote")
            if quote["expires_at"] <= self.at:
                self._release_quotes(request_id, QuoteStatus.REJECTED)
                self.conn.execute(
                    "UPDATE requests SET status = %(status)s WHERE id = %(id)s",
                    {"id": request_id, "status": RequestStatus.FAILED.value},
                )
                raise QuoteExpiredError(f"quote {quote['id']} expired")
            selected.append((leg, quote))

        for leg, quote in selected:
            req_amt = leg["notional"] * quote["price"]
            mm_amt = leg["notional"] * (Decimal("1") - quote["price"])
            self._ledger.lock_escrow(req["requester_id"], req_amt, quote["mm_id"], mm_amt)
            self.conn.execute(
                """
                INSERT INTO escrows (id, leg_id, requester_id, mm_id, requester_locked, mm_locked)
                VALUES (%(id)s, %(leg_id)s, %(requester_id)s, %(mm_id)s, %(req)s, %(mm)s)
                """,
                {
                    "id": uuid.uuid4(),
                    "leg_id": leg["id"],
                    "requester_id": req["requester_id"],
                    "mm_id": quote["mm_id"],
                    "req": req_amt,
                    "mm": mm_amt,
                },
            )

        selected_ids = {q["id"] for _, q in selected}
        self._reject_competing(request_id, selected_ids)
        self.conn.execute(
            "UPDATE requests SET status = %(status)s WHERE id = %(id)s",
            {"id": request_id, "status": RequestStatus.ESCROW_LOCKED.value},
        )

    def reject(self, request_id: UUID) -> None:
        req = self.conn.execute(
            "SELECT * FROM requests WHERE id = %(id)s FOR UPDATE",
            {"id": request_id},
        ).fetchone()
        if req is None:
            raise NotFoundError(f"request {request_id} not found")
        if req["status"] != RequestStatus.PRESENTED.value:
            raise InvalidStateError(f"cannot reject in status {req['status']}")
        self._release_quotes(request_id, QuoteStatus.REJECTED)
        self.conn.execute(
            "UPDATE requests SET status = %(status)s WHERE id = %(id)s",
            {"id": request_id, "status": RequestStatus.REJECTED.value},
        )

    def process_expirations(self) -> list[UUID]:
        expired: list[UUID] = []
        rows = self.conn.execute(
            """
            SELECT id FROM requests
            WHERE status = %(status)s AND accept_deadline < %(at)s
            FOR UPDATE
            """,
            {"status": RequestStatus.PRESENTED.value, "at": self.at},
        ).fetchall()
        for row in rows:
            self._release_quotes(row["id"], QuoteStatus.EXPIRED)
            self.conn.execute(
                "UPDATE requests SET status = %(status)s WHERE id = %(id)s",
                {"id": row["id"], "status": RequestStatus.EXPIRED.value},
            )
            expired.append(row["id"])
        return expired

    def initiate_resolution(self, request_id: UUID) -> None:
        req = self.conn.execute(
            "SELECT status FROM requests WHERE id = %(id)s",
            {"id": request_id},
        ).fetchone()
        if req is None:
            raise NotFoundError(f"request {request_id} not found")
        if req["status"] != RequestStatus.ESCROW_LOCKED.value:
            raise InvalidStateError(f"cannot resolve in status {req['status']}")

        legs = self.conn.execute(
            "SELECT id FROM legs WHERE request_id = %(id)s",
            {"id": request_id},
        ).fetchall()
        for leg in legs:
            self.conn.execute(
                """
                INSERT INTO resolutions (id, leg_id, status)
                VALUES (%(id)s, %(leg_id)s, %(status)s)
                """,
                {
                    "id": uuid.uuid4(),
                    "leg_id": leg["id"],
                    "status": ResolutionStatus.PENDING.value,
                },
            )
        self.conn.execute(
            "UPDATE requests SET status = %(status)s WHERE id = %(id)s",
            {"id": request_id, "status": RequestStatus.RESOLVED.value},
        )

    def resolve_leg(self, leg_id: UUID, outcome: ResolutionOutcome) -> None:
        res = self.conn.execute(
            "SELECT * FROM resolutions WHERE leg_id = %(leg_id)s FOR UPDATE",
            {"leg_id": leg_id},
        ).fetchone()
        if res is None:
            raise NotFoundError(f"no resolution for leg {leg_id}")
        if res["status"] == ResolutionStatus.RESOLVED.value:
            return

        escrow = self.conn.execute(
            "SELECT * FROM escrows WHERE leg_id = %(leg_id)s",
            {"leg_id": leg_id},
        ).fetchone()
        winner = (
            escrow["requester_id"]
            if outcome == ResolutionOutcome.YES
            else escrow["mm_id"]
        )
        self._ledger.payout(
            escrow["requester_id"],
            escrow["requester_locked"],
            escrow["mm_id"],
            escrow["mm_locked"],
            winner,
        )
        self.conn.execute(
            """
            UPDATE resolutions SET status = %(status)s, outcome = %(outcome)s
            WHERE leg_id = %(leg_id)s
            """,
            {
                "leg_id": leg_id,
                "status": ResolutionStatus.RESOLVED.value,
                "outcome": outcome.value,
            },
        )

    def settle_request(self, request_id: UUID) -> None:
        legs = self.conn.execute(
            "SELECT id FROM legs WHERE request_id = %(id)s",
            {"id": request_id},
        ).fetchall()
        for leg in legs:
            res = self.conn.execute(
                "SELECT status FROM resolutions WHERE leg_id = %(leg_id)s",
                {"leg_id": leg["id"]},
            ).fetchone()
            if res is None or res["status"] != ResolutionStatus.RESOLVED.value:
                raise InvalidStateError(f"leg {leg['id']} not resolved")
        self.conn.execute(
            "UPDATE requests SET status = %(status)s WHERE id = %(id)s",
            {"id": request_id, "status": RequestStatus.SETTLED.value},
        )

    def get_request_status(self, request_id: UUID) -> RequestStatus:
        row = self.conn.execute(
            "SELECT status FROM requests WHERE id = %(id)s",
            {"id": request_id},
        ).fetchone()
        if row is None:
            raise NotFoundError(f"request {request_id} not found")
        return RequestStatus(row["status"])

    def _best_quote(self, leg_id: UUID, notional: Decimal) -> dict | None:
        return self.conn.execute(
            """
            SELECT * FROM quotes
            WHERE leg_id = %(leg_id)s
              AND status = %(status)s
              AND expires_at > %(at)s
              AND size >= %(notional)s
            ORDER BY price ASC, size DESC, created_at ASC
            LIMIT 1
            """,
            {
                "leg_id": leg_id,
                "status": QuoteStatus.ACTIVE.value,
                "at": self.at,
                "notional": notional,
            },
        ).fetchone()

    def _release_quotes(self, request_id: UUID, final_status: QuoteStatus) -> None:
        legs = self.conn.execute(
            "SELECT id FROM legs WHERE request_id = %(id)s",
            {"id": request_id},
        ).fetchall()
        for leg in legs:
            quotes = self.conn.execute(
                "SELECT * FROM quotes WHERE leg_id = %(leg_id)s",
                {"leg_id": leg["id"]},
            ).fetchall()
            for quote in quotes:
                if quote["status"] in (QuoteStatus.ACTIVE.value, QuoteStatus.SELECTED.value):
                    self._ledger.release_reservation(quote["mm_id"], quote["reserved_amount"])
                    self.conn.execute(
                        "UPDATE quotes SET status = %(status)s WHERE id = %(id)s",
                        {"id": quote["id"], "status": final_status.value},
                    )

    def _reject_competing(self, request_id: UUID, selected_ids: set[UUID]) -> None:
        legs = self.conn.execute(
            "SELECT id FROM legs WHERE request_id = %(id)s",
            {"id": request_id},
        ).fetchall()
        for leg in legs:
            quotes = self.conn.execute(
                """
                SELECT * FROM quotes
                WHERE leg_id = %(leg_id)s AND status = %(status)s
                """,
                {"leg_id": leg["id"], "status": QuoteStatus.ACTIVE.value},
            ).fetchall()
            for quote in quotes:
                if quote["id"] not in selected_ids:
                    self._ledger.release_reservation(quote["mm_id"], quote["reserved_amount"])
                    self.conn.execute(
                        "UPDATE quotes SET status = %(status)s WHERE id = %(id)s",
                        {"id": quote["id"], "status": QuoteStatus.REJECTED.value},
                    )
