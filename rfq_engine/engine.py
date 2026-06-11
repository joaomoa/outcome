import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from rfq_engine.queries import Queries

ACCEPT_WINDOW_SECONDS = 300.0


@dataclass
class LegInput:
    contract_description: str
    notional: Decimal


class RfqEngine:
    def __init__(self, conn: psycopg.Connection, at: datetime | None = None) -> None:
        self.conn = conn
        self._default_at = at
        self._ledger = Ledger(conn)
        self._db = Queries(conn)

    def _now(self, at: datetime | None = None) -> datetime:
        if at is not None:
            return at
        if self._default_at is not None:
            return self._default_at
        return datetime.now(timezone.utc)

    def create_participant(self, name: str, initial_balance: Decimal) -> UUID:
        with self.conn.transaction():
            participant_id = uuid.uuid4()
            self._db.insert_participant(participant_id, name, initial_balance)
        return participant_id

    def submit_request(
        self,
        requester_id: UUID,
        legs: list[LegInput],
        response_deadline_seconds: float,
        *,
        at: datetime | None = None,
    ) -> UUID:
        now = self._now(at)
        with self.conn.transaction():
            request_id = uuid.uuid4()
            self._db.insert_request(
                request_id,
                requester_id,
                RequestStatus.QUOTING,
                now + timedelta(seconds=response_deadline_seconds),
            )
            for i, leg in enumerate(legs):
                self._db.insert_leg(
                    uuid.uuid4(),
                    request_id,
                    leg.contract_description,
                    leg.notional,
                    i,
                )
        return request_id

    def submit_quote(
        self,
        leg_id: UUID,
        mm_id: UUID,
        price: Decimal,
        size: Decimal,
        expires_in_seconds: float,
        *,
        at: datetime | None = None,
    ) -> UUID:
        now = self._now(at)
        with self.conn.transaction():
            leg = self._db.get_leg(leg_id)
            if leg is None:
                raise NotFoundError(f"leg {leg_id} not found")

            req = self._db.get_request_for_update(leg["request_id"])
            if req["status"] not in (RequestStatus.OPEN.value, RequestStatus.QUOTING.value):
                raise InvalidStateError(f"cannot quote in status {req['status']}")
            if now >= req["response_deadline"]:
                raise InvalidStateError("response deadline passed")
            if size < leg["notional"]:
                raise InvalidStateError(f"size {size} < notional {leg['notional']}")

            reserve = leg["notional"] * (Decimal("1") - price)
            quote_id = uuid.uuid4()
            self._db.insert_quote(
                quote_id,
                leg_id,
                mm_id,
                price,
                size,
                now + timedelta(seconds=expires_in_seconds),
                reserve,
            )
            self._ledger.reserve(mm_id, reserve)
        return quote_id

    def run_matching(self, request_id: UUID, *, at: datetime | None = None) -> RequestStatus:
        now = self._now(at)
        with self.conn.transaction():
            req = self._db.get_request_for_update(request_id)
            if req is None:
                raise NotFoundError(f"request {request_id} not found")
            if req["status"] not in (RequestStatus.QUOTING.value, RequestStatus.OPEN.value):
                raise InvalidStateError(f"cannot match in status {req['status']}")

            selected: list[dict] = []
            for leg in self._db.list_legs(request_id):
                best = self._db.get_best_quote(leg["id"], leg["notional"], now)
                if best is None:
                    self._db.update_request_status(request_id, RequestStatus.FAILED)
                    return RequestStatus.FAILED
                selected.append(best)

            for quote in selected:
                self._db.update_quote_status(quote["id"], QuoteStatus.SELECTED)
            self._db.update_request_presented(
                request_id,
                now + timedelta(seconds=ACCEPT_WINDOW_SECONDS),
            )
            return RequestStatus.PRESENTED

    def accept(self, request_id: UUID, *, at: datetime | None = None) -> None:
        now = self._now(at)
        quote_expired = False
        with self.conn.transaction():
            req = self._db.get_request_for_update(request_id)
            if req is None:
                raise NotFoundError(f"request {request_id} not found")
            if req["status"] != RequestStatus.PRESENTED.value:
                raise ConflictError(f"cannot accept in status {req['status']}")
            if req["accept_deadline"] is None or now > req["accept_deadline"]:
                raise QuoteExpiredError("accept window expired")

            selected: list[tuple[dict, dict]] = []
            for leg in self._db.list_legs(request_id):
                quote = self._db.get_selected_quote(leg["id"])
                if quote is None:
                    raise InvalidStateError(f"leg {leg['id']} has no selected quote")
                if quote["expires_at"] <= now:
                    self._release_quotes(request_id, QuoteStatus.REJECTED)
                    self._db.update_request_status(request_id, RequestStatus.FAILED)
                    quote_expired = True
                    break
                selected.append((leg, quote))

            if not quote_expired:
                for leg, quote in selected:
                    req_amt = leg["notional"] * quote["price"]
                    mm_amt = leg["notional"] * (Decimal("1") - quote["price"])
                    self._ledger.lock_escrow(req["requester_id"], req_amt, quote["mm_id"], mm_amt)
                    self._db.insert_escrow(
                        uuid.uuid4(),
                        leg["id"],
                        req["requester_id"],
                        quote["mm_id"],
                        req_amt,
                        mm_amt,
                    )
                selected_ids = {q["id"] for _, q in selected}
                self._reject_competing(request_id, selected_ids)
                self._db.update_request_status(request_id, RequestStatus.ESCROW_LOCKED)
        if quote_expired:
            raise QuoteExpiredError("quote expired before accept")

    def reject(self, request_id: UUID) -> None:
        with self.conn.transaction():
            req = self._db.get_request_for_update(request_id)
            if req is None:
                raise NotFoundError(f"request {request_id} not found")
            if req["status"] != RequestStatus.PRESENTED.value:
                raise InvalidStateError(f"cannot reject in status {req['status']}")
            self._release_quotes(request_id, QuoteStatus.REJECTED)
            self._db.update_request_status(request_id, RequestStatus.REJECTED)

    def process_expirations(self, *, at: datetime | None = None) -> list[UUID]:
        now = self._now(at)
        with self.conn.transaction():
            expired: list[UUID] = []
            for row in self._db.list_expired_presented_requests(now):
                self._release_quotes(row["id"], QuoteStatus.EXPIRED)
                self._db.update_request_status(row["id"], RequestStatus.EXPIRED)
                expired.append(row["id"])
            return expired

    def initiate_resolution(self, request_id: UUID) -> None:
        with self.conn.transaction():
            req = self._db.get_request(request_id)
            if req is None:
                raise NotFoundError(f"request {request_id} not found")
            if req["status"] != RequestStatus.ESCROW_LOCKED.value:
                raise InvalidStateError(f"cannot resolve in status {req['status']}")

            for leg in self._db.list_leg_ids(request_id):
                self._db.insert_resolution(uuid.uuid4(), leg["id"])
            self._db.update_request_status(request_id, RequestStatus.RESOLVED)

    def resolve_leg(self, leg_id: UUID, outcome: ResolutionOutcome) -> None:
        with self.conn.transaction():
            res = self._db.get_resolution_for_update(leg_id)
            if res is None:
                raise NotFoundError(f"no resolution for leg {leg_id}")
            if res["status"] == ResolutionStatus.RESOLVED.value:
                return

            escrow = self._db.get_escrow(leg_id)
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
            self._db.update_resolution(leg_id, ResolutionStatus.RESOLVED, outcome.value)

    def settle_request(self, request_id: UUID) -> None:
        with self.conn.transaction():
            for leg in self._db.list_leg_ids(request_id):
                res = self._db.get_resolution(leg["id"])
                if res is None or res["status"] != ResolutionStatus.RESOLVED.value:
                    raise InvalidStateError(f"leg {leg['id']} not resolved")
            self._db.update_request_status(request_id, RequestStatus.SETTLED)

    def get_request_status(self, request_id: UUID) -> RequestStatus:
        status = self._db.get_request_status(request_id)
        if status is None:
            raise NotFoundError(f"request {request_id} not found")
        return RequestStatus(status)

    def _release_quotes(self, request_id: UUID, final_status: QuoteStatus) -> None:
        for leg in self._db.list_leg_ids(request_id):
            for quote in self._db.list_quotes_for_leg(leg["id"]):
                if quote["status"] in (QuoteStatus.ACTIVE.value, QuoteStatus.SELECTED.value):
                    self._ledger.release_reservation(quote["mm_id"], quote["reserved_amount"])
                    self._db.update_quote_status(quote["id"], final_status)

    def _reject_competing(self, request_id: UUID, selected_ids: set[UUID]) -> None:
        for leg in self._db.list_leg_ids(request_id):
            for quote in self._db.list_active_quotes_for_leg(leg["id"]):
                if quote["id"] not in selected_ids:
                    self._ledger.release_reservation(quote["mm_id"], quote["reserved_amount"])
                    self._db.update_quote_status(quote["id"], QuoteStatus.REJECTED)
