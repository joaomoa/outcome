from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rfq_engine.enums import QuoteStatus, RequestStatus, ResolutionOutcome, ResolutionStatus
from rfq_engine.errors import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    QuoteExpiredError,
)
from rfq_engine.ledger import Ledger
from rfq_engine.models import Balance, Escrow, Leg, Participant, Quote, Request, Resolution
from rfq_engine.money import mm_collateral, requester_premium

ACCEPT_WINDOW_SECONDS = 300.0


@dataclass(frozen=True)
class LegInput:
    contract_description: str
    notional: Decimal


class RfqEngine:
    def __init__(self, session: Session, at: datetime) -> None:
        self.session = session
        self.at = at
        self._ledger = Ledger(session)

    def create_participant(self, name: str, initial_balance: Decimal) -> UUID:
        p = Participant(name=name)
        self.session.add(p)
        self.session.flush()
        self.session.add(
            Balance(
                participant_id=p.id,
                available=initial_balance,
                reserved=Decimal("0"),
                locked=Decimal("0"),
            )
        )
        return p.id

    def submit_request(
        self,
        requester_id: UUID,
        legs: list[LegInput],
        response_deadline_seconds: float,
    ) -> UUID:
        req = Request(
            requester_id=requester_id,
            status=RequestStatus.QUOTING.value,
            response_deadline=self.at + timedelta(seconds=response_deadline_seconds),
        )
        self.session.add(req)
        self.session.flush()
        for i, leg in enumerate(legs):
            self.session.add(
                Leg(
                    request_id=req.id,
                    contract_description=leg.contract_description,
                    notional=leg.notional,
                    leg_index=i,
                )
            )
        return req.id

    def submit_quote(
        self,
        leg_id: UUID,
        mm_id: UUID,
        price: Decimal,
        size: Decimal,
        expires_in_seconds: float,
    ) -> UUID:
        leg = self.session.get(Leg, leg_id)
        if leg is None:
            raise NotFoundError(f"leg {leg_id} not found")

        req = self.session.execute(
            select(Request).where(Request.id == leg.request_id).with_for_update()
        ).scalar_one()

        if req.status not in (RequestStatus.OPEN.value, RequestStatus.QUOTING.value):
            raise InvalidStateError(f"cannot quote in status {req.status}")

        if self.at >= req.response_deadline:
            raise InvalidStateError("response deadline passed")
        if size < leg.notional:
            raise InvalidStateError(f"size {size} < notional {leg.notional}")

        reserve = mm_collateral(leg.notional, price)
        quote = Quote(
            leg_id=leg_id,
            mm_id=mm_id,
            price=price,
            size=size,
            expires_at=self.at + timedelta(seconds=expires_in_seconds),
            status=QuoteStatus.ACTIVE.value,
            reserved_amount=reserve,
        )
        self.session.add(quote)
        self.session.flush()
        self._ledger.reserve(mm_id, reserve)
        return quote.id

    def run_matching(self, request_id: UUID) -> RequestStatus:
        req = self.session.execute(
            select(Request).where(Request.id == request_id).with_for_update()
        ).scalar_one_or_none()
        if req is None:
            raise NotFoundError(f"request {request_id} not found")
        if req.status not in (RequestStatus.QUOTING.value, RequestStatus.OPEN.value):
            raise InvalidStateError(f"cannot match in status {req.status}")

        legs = self.session.execute(
            select(Leg).where(Leg.request_id == request_id).order_by(Leg.leg_index)
        ).scalars().all()

        selected: list[Quote] = []
        for leg in legs:
            best = self._best_quote(leg.id, leg.notional)
            if best is None:
                req.status = RequestStatus.FAILED.value
                return RequestStatus.FAILED
            selected.append(best)

        for q in selected:
            q.status = QuoteStatus.SELECTED.value
        req.status = RequestStatus.PRESENTED.value
        req.accept_deadline = self.at + timedelta(seconds=ACCEPT_WINDOW_SECONDS)
        return RequestStatus.PRESENTED

    def accept(self, request_id: UUID) -> None:
        req = self.session.execute(
            select(Request).where(Request.id == request_id).with_for_update()
        ).scalar_one_or_none()
        if req is None:
            raise NotFoundError(f"request {request_id} not found")
        if req.status != RequestStatus.PRESENTED.value:
            raise ConflictError(f"cannot accept in status {req.status}")

        if req.accept_deadline is None or self.at > req.accept_deadline:
            raise QuoteExpiredError("accept window expired")

        legs = self.session.execute(
            select(Leg).where(Leg.request_id == request_id).order_by(Leg.leg_index)
        ).scalars().all()

        selected: list[tuple[Leg, Quote]] = []
        for leg in legs:
            quote = self.session.execute(
                select(Quote)
                .where(Quote.leg_id == leg.id)
                .where(Quote.status == QuoteStatus.SELECTED.value)
            ).scalar_one_or_none()
            if quote is None:
                raise InvalidStateError(f"leg {leg.id} has no selected quote")
            if quote.expires_at <= self.at:
                self._release_quotes(request_id, QuoteStatus.REJECTED)
                req.status = RequestStatus.FAILED.value
                raise QuoteExpiredError(f"quote {quote.id} expired")
            selected.append((leg, quote))

        for leg, quote in selected:
            req_amt = requester_premium(leg.notional, quote.price)
            mm_amt = mm_collateral(leg.notional, quote.price)
            self._ledger.lock_escrow(req.requester_id, req_amt, quote.mm_id, mm_amt)
            self.session.add(
                Escrow(
                    leg_id=leg.id,
                    requester_id=req.requester_id,
                    mm_id=quote.mm_id,
                    requester_locked=req_amt,
                    mm_locked=mm_amt,
                )
            )

        selected_ids = {q.id for _, q in selected}
        self._reject_competing(request_id, selected_ids)
        req.status = RequestStatus.ESCROW_LOCKED.value

    def reject(self, request_id: UUID) -> None:
        req = self.session.execute(
            select(Request).where(Request.id == request_id).with_for_update()
        ).scalar_one_or_none()
        if req is None:
            raise NotFoundError(f"request {request_id} not found")
        if req.status != RequestStatus.PRESENTED.value:
            raise InvalidStateError(f"cannot reject in status {req.status}")
        self._release_quotes(request_id, QuoteStatus.REJECTED)
        req.status = RequestStatus.REJECTED.value

    def process_expirations(self) -> list[UUID]:
        expired: list[UUID] = []
        for req in self.session.execute(
            select(Request)
            .where(Request.status == RequestStatus.PRESENTED.value)
            .where(Request.accept_deadline < self.at)
            .with_for_update()
        ).scalars().all():
            self._release_quotes(req.id, QuoteStatus.EXPIRED)
            req.status = RequestStatus.EXPIRED.value
            expired.append(req.id)
        return expired

    def initiate_resolution(self, request_id: UUID) -> None:
        req = self.session.get(Request, request_id)
        if req is None:
            raise NotFoundError(f"request {request_id} not found")
        if req.status != RequestStatus.ESCROW_LOCKED.value:
            raise InvalidStateError(f"cannot resolve in status {req.status}")
        for leg in self.session.execute(
            select(Leg).where(Leg.request_id == request_id)
        ).scalars().all():
            self.session.add(Resolution(leg_id=leg.id, status=ResolutionStatus.PENDING.value))
        req.status = RequestStatus.RESOLVED.value

    def resolve_leg(self, leg_id: UUID, outcome: ResolutionOutcome) -> None:
        leg = self.session.execute(
            select(Leg).where(Leg.id == leg_id).with_for_update()
        ).scalar_one_or_none()
        if leg is None:
            raise NotFoundError(f"leg {leg_id} not found")

        res = self.session.execute(
            select(Resolution).where(Resolution.leg_id == leg_id).with_for_update()
        ).scalar_one_or_none()
        if res is None:
            raise NotFoundError(f"no resolution for leg {leg_id}")
        if res.status == ResolutionStatus.RESOLVED.value:
            return

        escrow = self.session.execute(
            select(Escrow).where(Escrow.leg_id == leg_id)
        ).scalar_one()
        winner = (
            escrow.requester_id
            if outcome == ResolutionOutcome.YES
            else escrow.mm_id
        )
        self._ledger.payout(
            escrow.requester_id,
            escrow.requester_locked,
            escrow.mm_id,
            escrow.mm_locked,
            winner,
        )
        res.status = ResolutionStatus.RESOLVED.value
        res.outcome = outcome.value

    def settle_request(self, request_id: UUID) -> None:
        req = self.session.execute(
            select(Request).where(Request.id == request_id).with_for_update()
        ).scalar_one_or_none()
        if req is None:
            raise NotFoundError(f"request {request_id} not found")
        for leg in self.session.execute(
            select(Leg).where(Leg.request_id == request_id)
        ).scalars().all():
            res = self.session.execute(
                select(Resolution).where(Resolution.leg_id == leg.id)
            ).scalar_one()
            if res.status != ResolutionStatus.RESOLVED.value:
                raise InvalidStateError(f"leg {leg.id} not resolved")
        req.status = RequestStatus.SETTLED.value

    def get_request_status(self, request_id: UUID) -> RequestStatus:
        req = self.session.get(Request, request_id)
        if req is None:
            raise NotFoundError(f"request {request_id} not found")
        return RequestStatus(req.status)

    def _best_quote(self, leg_id: UUID, notional: Decimal) -> Quote | None:
        quotes = self.session.execute(
            select(Quote)
            .where(Quote.leg_id == leg_id)
            .where(Quote.status == QuoteStatus.ACTIVE.value)
            .where(Quote.expires_at > self.at)
            .where(Quote.size >= notional)
            .order_by(Quote.price.asc(), Quote.size.desc(), Quote.created_at.asc())
        ).scalars().all()
        return quotes[0] if quotes else None

    def _release_quotes(self, request_id: UUID, final_status: QuoteStatus) -> None:
        for leg in self.session.execute(
            select(Leg).where(Leg.request_id == request_id)
        ).scalars().all():
            for quote in self.session.execute(
                select(Quote).where(Quote.leg_id == leg.id)
            ).scalars().all():
                if quote.status in (QuoteStatus.ACTIVE.value, QuoteStatus.SELECTED.value):
                    self._ledger.release_reservation(quote.mm_id, quote.reserved_amount)
                    quote.status = final_status.value

    def _reject_competing(self, request_id: UUID, selected_ids: set[UUID]) -> None:
        for leg in self.session.execute(
            select(Leg).where(Leg.request_id == request_id)
        ).scalars().all():
            for quote in self.session.execute(
                select(Quote)
                .where(Quote.leg_id == leg.id)
                .where(Quote.status == QuoteStatus.ACTIVE.value)
            ).scalars().all():
                if quote.id not in selected_ids:
                    quote.status = QuoteStatus.REJECTED.value
                    self._ledger.release_reservation(quote.mm_id, quote.reserved_amount)
