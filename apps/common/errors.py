"""Shared domain errors. Services raise these; views/tests assert them."""


class DomainError(Exception):
    pass


class PermissionDeniedError(DomainError):
    pass


class ValidationError(DomainError):
    pass


class StaleVersionError(DomainError):
    pass


class NegativeBalanceError(DomainError):
    pass


class InsufficientStockError(DomainError):
    """Raised without writing anything. Carries an explicit revised proposal."""

    def __init__(self, shortages, proposal):
        super().__init__("insufficient stock")
        self.shortages = shortages
        self.proposal = proposal


class AlreadyReversedError(DomainError):
    pass


class MaintenanceActiveError(DomainError):
    pass


class ThrottledError(DomainError):
    pass
