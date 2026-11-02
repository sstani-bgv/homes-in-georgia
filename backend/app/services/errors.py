class DomainError(Exception):
    """Business-rule violation. The API maps `status` to the HTTP code; `code` is a stable machine string."""

    STATUS = {"not_found": 404, "forbidden": 403, "not_registered": 403, "already_taken": 409, "taken_by_other": 409,
              "conflict": 409, "bad_status": 409, "unauthorized": 401}

    def __init__(self, code: str, message: str = "", status: int | None = None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status = status or self.STATUS.get(code, 400)


def not_found(what: str = "object"):
    return DomainError("not_found", f"{what} not found")


def forbidden(message: str = "forbidden"):
    return DomainError("forbidden", message)
