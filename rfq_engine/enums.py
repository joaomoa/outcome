from enum import StrEnum


class RequestStatus(StrEnum):
    OPEN = "open"
    QUOTING = "quoting"
    PRESENTED = "presented"
    ESCROW_LOCKED = "escrow_locked"
    RESOLVED = "resolved"
    SETTLED = "settled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class QuoteStatus(StrEnum):
    ACTIVE = "active"
    SELECTED = "selected"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ResolutionStatus(StrEnum):
    PENDING = "pending"
    PROPOSED = "proposed"
    DISPUTED = "disputed"
    RESOLVED = "resolved"


class ResolutionOutcome(StrEnum):
    YES = "yes"
    NO = "no"
    VOID = "void"
