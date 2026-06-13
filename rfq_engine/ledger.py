from decimal import Decimal
from uuid import UUID

import psycopg

from rfq_engine.errors import InsufficientFundsError


class Ledger:
    """Balance mutations — every statement is explicit SQL."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def lock_parlay_escrow(
        self,
        requester_id: UUID,
        premium: Decimal,
        mm_id: UUID,
        collateral: Decimal,
    ) -> None:
        req = self.conn.execute(
            """
            UPDATE balances
            SET available = available - %(amount)s,
                locked = locked + %(amount)s
            WHERE participant_id = %(participant_id)s
              AND available >= %(amount)s
            RETURNING participant_id
            """,
            {"participant_id": requester_id, "amount": premium},
        ).fetchone()
        if req is None:
            raise InsufficientFundsError("requester insufficient funds")

        mm = self.conn.execute(
            """
            UPDATE balances
            SET available = available - %(amount)s,
                locked = locked + %(amount)s
            WHERE participant_id = %(participant_id)s
              AND available >= %(amount)s
            RETURNING participant_id
            """,
            {"participant_id": mm_id, "amount": collateral},
        ).fetchone()
        if mm is None:
            raise InsufficientFundsError("MM insufficient collateral")

    def payout(
        self,
        requester_id: UUID,
        requester_locked: Decimal,
        mm_id: UUID,
        mm_locked: Decimal,
        winner_id: UUID,
    ) -> None:
        total = requester_locked + mm_locked
        if winner_id == requester_id:
            self.conn.execute(
                """
                UPDATE balances
                SET locked = locked - %(req_locked)s,
                    available = available + %(total)s
                WHERE participant_id = %(participant_id)s
                """,
                {
                    "participant_id": requester_id,
                    "req_locked": requester_locked,
                    "total": total,
                },
            )
            self.conn.execute(
                """
                UPDATE balances
                SET locked = locked - %(mm_locked)s
                WHERE participant_id = %(participant_id)s
                """,
                {"participant_id": mm_id, "mm_locked": mm_locked},
            )
        elif winner_id == mm_id:
            self.conn.execute(
                """
                UPDATE balances
                SET locked = locked - %(req_locked)s
                WHERE participant_id = %(participant_id)s
                """,
                {"participant_id": requester_id, "req_locked": requester_locked},
            )
            self.conn.execute(
                """
                UPDATE balances
                SET locked = locked - %(mm_locked)s,
                    available = available + %(total)s
                WHERE participant_id = %(participant_id)s
                """,
                {"participant_id": mm_id, "mm_locked": mm_locked, "total": total},
            )
        else:
            raise ValueError(f"unknown winner {winner_id}")

    def refund_escrow(
        self,
        requester_id: UUID,
        requester_locked: Decimal,
        mm_id: UUID,
        mm_locked: Decimal,
    ) -> None:
        self.conn.execute(
            """
            UPDATE balances
            SET locked = locked - %(amount)s,
                available = available + %(amount)s
            WHERE participant_id = %(participant_id)s
            """,
            {"participant_id": requester_id, "amount": requester_locked},
        )
        self.conn.execute(
            """
            UPDATE balances
            SET locked = locked - %(amount)s,
                available = available + %(amount)s
            WHERE participant_id = %(participant_id)s
            """,
            {"participant_id": mm_id, "amount": mm_locked},
        )
