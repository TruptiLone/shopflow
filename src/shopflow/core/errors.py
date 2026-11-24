class DomainError(Exception):
    """Base for business-rule errors. Routers never raise HTTPException directly."""

    status_code = 400
    code = "domain_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class InvalidState(DomainError):
    status_code = 409
    code = "invalid_state"


class OutOfStock(DomainError):
    status_code = 409
    code = "out_of_stock"


class ValidationFailed(DomainError):
    status_code = 422
    code = "validation_failed"
