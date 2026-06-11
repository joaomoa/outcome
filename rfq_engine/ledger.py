from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rfq_engine.errors import InsufficientFundsError
from rfq_engine.models import Balance


class Ledger:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _balance(self, participant_id: UUID) -> Balance:
        return self.session.execute(
            select(Balance).where(Balance.participant_id == participant_id).with_for_update()
        ).scalar_one()

    def reserve(self, participant_id: UUID, amount: Decimal) -> None:
        b = self._balance(participant_id)
        if b.available < amount:
            raise InsufficientFundsError(f"need {amount}, have {b.available}")
        b.available -= amount
        b.reserved += amount

    def release_reservation(self, participant_id: UUID, amount: Decimal) -> None:
        b = self._balance(participant_id)
        if b.reserved < amount:
            raise InsufficientFundsError(f"cannot release {amount}, reserved {b.reserved}")
        b.reserved -= amount
        b.available += amount

    def lock_escrow(
        self,
        requester_id: UUID,
        requester_amount: Decimal,
        mm_id: UUID,
        mm_amount: Decimal,
    ) -> None:
        requester = self._balance(requester_id)
        mm = self._balance(mm_id)
        if requester.available < requester_amount:
            raise InsufficientFundsError("requester insufficient funds")
        if mm.reserved < mm_amount:
            raise InsufficientFundsError("MM insufficient reserved collateral")
        requester.available -= requester_amount
        requester.locked += requester_amount
        mm.reserved -= mm_amount
        mm.locked += mm_amount

    def payout(
        self,
        requester_id: UUID,
        requester_locked: Decimal,
        mm_id: UUID,
        mm_locked: Decimal,
        winner_id: UUID,
    ) -> None:
        requester = self._balance(requester_id)
        mm = self._balance(mm_id)
        total = requester_locked + mm_locked
        if winner_id == requester_id:
            requester.locked -= requester_locked
            mm.locked -= mm_locked
            requester.available += total
        elif winner_id == mm_id:
            requester.locked -= requester_locked
            mm.locked -= mm_locked
            mm.available += total
        else:
            raise ValueError(f"unknown winner {winner_id}")
