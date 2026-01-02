"""Application-specific exceptions.

This module currently defines :class:`ValidationError`, a lightweight exception
used by :mod:`app.utils.validation` to signal validation failures.
"""

from __future__ import annotations


class ValidationError(ValueError):
    """Raised when input validation fails.

    This exception is intended to be raised by helpers in
    :mod:`app.utils.validation` and caught by request handlers to produce an
    appropriate HTTP response.

    Parameters
    ----------
    message:
        Human-readable error message.
    field:
        Optional name of the field/parameter that failed validation.
    code:
        Optional machine-readable error code.
    details:
        Optional extra context (e.g. structured errors).
    """

    def __init__(
        self,
        message: str = "Validation error",
        *,
        field: str | None = None,
        code: str | None = None,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.code = code
        self.details = details

    def to_dict(self) -> dict:
        """Serialize the error into a JSON-friendly dictionary."""

        data = {"message": self.message}
        if self.field is not None:
            data["field"] = self.field
        if self.code is not None:
            data["code"] = self.code
        if self.details is not None:
            data["details"] = self.details
        return data
