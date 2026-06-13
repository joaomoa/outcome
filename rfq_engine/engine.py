import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import psycopg

from rfq_engine.enums import QuoteStatus, RequestStatus, ResolutionOutcome, ResolutionStatus
from rfq_engine.errors import (
    ConflictError,
    DisputeWindowExpiredError,
    InvalidStateError,
    NotFoundError,
    QuoteExpiredError,
)
from rfq_engine.ledger import Ledger
from rfq_engine.queries import Queries

ACCEPT_WINDOW_SECONDS = 300.0
DISPUTE_WINDOW_SECONDS = 2 * 3600.0


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

            quote_id = uuid.uuid4()
            self._db.insert_quote(
                quote_id,
                leg_id,
                mm_id,
                price,
                size,
                now + timedelta(seconds=expires_in_seconds),
            )
        return quote_id

    def run_matching(self, request_id: UUID, *, at: datetime | None = None) -> RequestStatus:
        now = self._now(at)
        with self.conn.transaction():
            req = self._db.get_request_for_update(request_id)
            if req is None:
                raise NotFoundError(f"request {request_id} not found")
            if req["status"] not in (RequestStatus.QUOTING.value, RequestStatus.OPEN.value):
                raise InvalidStateError(f"cannot match in status {req['status']}")

            legs = self._db.list_legs(request_id)
            package = self._select_best_parlay_package(legs, now)
            if package is None:
                self._db.update_request_status(request_id, RequestStatus.FAILED)
                return RequestStatus.FAILED

            parlay_price = Decimal("1")
            for quote in package:
                self._db.update_quote_status(quote["id"], QuoteStatus.SELECTED)
                parlay_price *= quote["price"]
            self._db.update_request_presented(
                request_id,
                now + timedelta(seconds=ACCEPT_WINDOW_SECONDS),
                parlay_price,
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
                legs_and_quotes = selected
                mm_id = selected[0][1]["mm_id"]
                _, _, premium, collateral = self._parlay_capital(legs_and_quotes)

                self._ledger.lock_parlay_escrow(
                    req["requester_id"], premium, mm_id, collateral
                )
                self._db.insert_escrow(
                    uuid.uuid4(),
                    request_id,
                    req["requester_id"],
                    mm_id,
                    premium,
                    collateral,
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

    def process_resolution_expirations(self, *, at: datetime | None = None) -> list[UUID]:
        now = self._now(at)
        with self.conn.transaction():
            finalized: list[UUID] = []
            for row in self._db.list_expired_proposed_resolutions(now):
                request_id = row["request_id"]
                outcome = ResolutionOutcome(row["outcome"])
                self._apply_parlay_outcome(request_id, outcome)
                self._db.update_resolution(
                    request_id, ResolutionStatus.RESOLVED, outcome.value
                )
                finalized.append(request_id)
            return finalized

    def initiate_resolution(self, request_id: UUID) -> None:
        with self.conn.transaction():
            req = self._db.get_request(request_id)
            if req is None:
                raise NotFoundError(f"request {request_id} not found")
            if req["status"] != RequestStatus.ESCROW_LOCKED.value:
                raise InvalidStateError(f"cannot resolve in status {req['status']}")

            self._db.insert_resolution(uuid.uuid4(), request_id)
            self._db.update_request_status(request_id, RequestStatus.RESOLVED)

    def report_leg_outcome(self, leg_id: UUID, outcome: ResolutionOutcome) -> None:
        with self.conn.transaction():
            leg = self._db.get_leg(leg_id)
            if leg is None:
                raise NotFoundError(f"leg {leg_id} not found")
            req = self._db.get_request(leg["request_id"])
            if req is None or req["status"] != RequestStatus.RESOLVED.value:
                raise InvalidStateError("cannot report leg outcome outside resolution")
            res = self._db.get_resolution(leg["request_id"])
            if res is None or res["status"] != ResolutionStatus.PENDING.value:
                raise InvalidStateError(
                    f"cannot report leg outcome in resolution status {res['status'] if res else 'none'}"
                )
            if leg["component_outcome"] is not None:
                raise InvalidStateError(f"leg {leg_id} already has component outcome")
            self._db.set_leg_component_outcome(leg_id, outcome.value)

    def propose_outcome(self, request_id: UUID, *, at: datetime | None = None) -> None:
        now = self._now(at)
        with self.conn.transaction():
            res = self._db.get_resolution_for_update(request_id)
            if res is None:
                raise NotFoundError(f"no resolution for request {request_id}")
            if res["status"] != ResolutionStatus.PENDING.value:
                raise InvalidStateError(
                    f"cannot propose outcome for request {request_id} in status {res['status']}"
                )
            outcome = self._compute_parlay_outcome(request_id)
            self._db.propose_resolution(
                request_id,
                outcome.value,
                now + timedelta(seconds=DISPUTE_WINDOW_SECONDS),
            )

    def dispute_request(self, request_id: UUID, *, at: datetime | None = None) -> None:
        now = self._now(at)
        with self.conn.transaction():
            res = self._db.get_resolution_for_update(request_id)
            if res is None:
                raise NotFoundError(f"no resolution for request {request_id}")
            if res["status"] != ResolutionStatus.PROPOSED.value:
                raise InvalidStateError(
                    f"cannot dispute request {request_id} in status {res['status']}"
                )
            if res["dispute_deadline"] is None or now > res["dispute_deadline"]:
                raise DisputeWindowExpiredError("dispute window expired")
            self._db.update_resolution_status(request_id, ResolutionStatus.DISPUTED)

    def finalize_request(self, request_id: UUID) -> None:
        with self.conn.transaction():
            res = self._db.get_resolution_for_update(request_id)
            if res is None:
                raise NotFoundError(f"no resolution for request {request_id}")
            if res["status"] == ResolutionStatus.RESOLVED.value:
                return
            if res["status"] != ResolutionStatus.PROPOSED.value:
                raise InvalidStateError(
                    f"cannot finalize request {request_id} in status {res['status']}"
                )
            outcome = ResolutionOutcome(res["outcome"])
            self._apply_parlay_outcome(request_id, outcome)
            self._db.update_resolution(request_id, ResolutionStatus.RESOLVED, outcome.value)

    def resolve_request(self, request_id: UUID, outcome: ResolutionOutcome) -> None:
        with self.conn.transaction():
            res = self._db.get_resolution_for_update(request_id)
            if res is None:
                raise NotFoundError(f"no resolution for request {request_id}")
            if res["status"] == ResolutionStatus.RESOLVED.value:
                return
            if res["status"] != ResolutionStatus.DISPUTED.value:
                raise InvalidStateError(
                    f"cannot resolve request {request_id} in status {res['status']}"
                )
            self._apply_parlay_outcome(request_id, outcome)
            self._db.update_resolution(request_id, ResolutionStatus.RESOLVED, outcome.value)

    def settle_request(self, request_id: UUID) -> None:
        with self.conn.transaction():
            res = self._db.get_resolution(request_id)
            if res is None or res["status"] != ResolutionStatus.RESOLVED.value:
                raise InvalidStateError(f"request {request_id} not resolved")
            self._db.update_request_status(request_id, RequestStatus.SETTLED)

    def get_request_status(self, request_id: UUID) -> RequestStatus:
        status = self._db.get_request_status(request_id)
        if status is None:
            raise NotFoundError(f"request {request_id} not found")
        return RequestStatus(status)

    def _compute_parlay_outcome(self, request_id: UUID) -> ResolutionOutcome:
        legs = self._db.list_legs(request_id)
        if not legs:
            raise InvalidStateError(f"request {request_id} has no legs")
        components: list[ResolutionOutcome] = []
        for leg in legs:
            if leg["component_outcome"] is None:
                raise InvalidStateError(
                    f"leg {leg['id']} has no component outcome; parlay cannot be proposed yet"
                )
            components.append(ResolutionOutcome(leg["component_outcome"]))
        if ResolutionOutcome.VOID in components:
            return ResolutionOutcome.VOID
        if all(c == ResolutionOutcome.YES for c in components):
            return ResolutionOutcome.YES
        return ResolutionOutcome.NO

    def _apply_parlay_outcome(self, request_id: UUID, outcome: ResolutionOutcome) -> None:
        for escrow in self._db.list_escrows_for_request(request_id):
            if outcome == ResolutionOutcome.VOID:
                self._ledger.refund_escrow(
                    escrow["requester_id"],
                    escrow["requester_locked"],
                    escrow["mm_id"],
                    escrow["mm_locked"],
                )
            else:
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

    def _select_best_parlay_package(
        self, legs: list[dict], now: datetime
    ) -> list[dict] | None:
        if not legs:
            return None

        quotes_by_leg: dict[UUID, list[dict]] = {}
        for leg in legs:
            valid = [
                q
                for q in self._db.list_active_quotes_for_leg(leg["id"])
                if q["expires_at"] > now and q["size"] >= leg["notional"]
            ]
            if not valid:
                return None
            quotes_by_leg[leg["id"]] = valid

        common_mms = {
            q["mm_id"] for q in quotes_by_leg[legs[0]["id"]]
        }
        for leg in legs[1:]:
            common_mms &= {q["mm_id"] for q in quotes_by_leg[leg["id"]]}
        if not common_mms:
            return None

        best_package: list[dict] | None = None
        best_key: tuple | None = None
        for mm_id in common_mms:
            package: list[dict] = []
            product = Decimal("1")
            min_size: Decimal | None = None
            earliest = None
            for leg in legs:
                mm_quotes = sorted(
                    (q for q in quotes_by_leg[leg["id"]] if q["mm_id"] == mm_id),
                    key=lambda q: (q["price"], -q["size"], q["created_at"]),
                )
                quote = mm_quotes[0]
                package.append(quote)
                product *= quote["price"]
                min_size = quote["size"] if min_size is None else min(min_size, quote["size"])
                earliest = (
                    quote["created_at"]
                    if earliest is None
                    else min(earliest, quote["created_at"])
                )
            key = (product, -min_size, earliest, str(mm_id))
            if best_key is None or key < best_key:
                best_key = key
                best_package = package

        return best_package

    def _parlay_capital(
        self, legs_and_quotes: list[tuple[dict, dict]]
    ) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        total_notional = Decimal("0")
        parlay_price = Decimal("1")
        for leg, quote in legs_and_quotes:
            total_notional += leg["notional"]
            parlay_price *= quote["price"]
        premium = total_notional * parlay_price
        collateral = total_notional * (Decimal("1") - parlay_price)
        return total_notional, parlay_price, premium, collateral

    def _release_quotes(self, request_id: UUID, final_status: QuoteStatus) -> None:
        for leg in self._db.list_leg_ids(request_id):
            for quote in self._db.list_quotes_for_leg(leg["id"]):
                if quote["status"] in (QuoteStatus.ACTIVE.value, QuoteStatus.SELECTED.value):
                    self._db.update_quote_status(quote["id"], final_status)

    def _reject_competing(self, request_id: UUID, selected_ids: set[UUID]) -> None:
        for leg in self._db.list_leg_ids(request_id):
            for quote in self._db.list_active_quotes_for_leg(leg["id"]):
                if quote["id"] not in selected_ids:
                    self._db.update_quote_status(quote["id"], QuoteStatus.REJECTED)
