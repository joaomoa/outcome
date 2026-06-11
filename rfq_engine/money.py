"""Binary YES contract capital: requester pays N*p, MM posts N*(1-p)."""

from decimal import Decimal


def requester_premium(notional: Decimal, price: Decimal) -> Decimal:
    return notional * price


def mm_collateral(notional: Decimal, price: Decimal) -> Decimal:
    return notional * (Decimal("1") - price)
