class RFQError(Exception):
    pass


class InsufficientFundsError(RFQError):
    pass


class InvalidStateError(RFQError):
    pass


class QuoteExpiredError(RFQError):
    pass


class NotFoundError(RFQError):
    pass


class DisputeWindowExpiredError(RFQError):
    pass


class ConflictError(RFQError):
    pass
