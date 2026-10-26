"""Application errors.

Every error that should reach the user carries a stable machine-readable
``code`` (the frontend switches on it) and a human-readable ``message``.
"""

from __future__ import annotations


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def to_detail(self) -> dict:
        return {"code": self.code, "message": self.message}


def invalid_input(message: str) -> AppError:
    return AppError("INVALID_INPUT", message, 400)


def not_found(message: str, code: str = "NOT_FOUND") -> AppError:
    return AppError(code, message, 404)


def too_large(message: str) -> AppError:
    return AppError("TOO_LARGE", message, 413)


def unsafe_archive(message: str) -> AppError:
    return AppError("ARCHIVE_UNSAFE", message, 422)


def parse_error(message: str, code: str = "PARSE_ERROR") -> AppError:
    return AppError(code, message, 422)


def source_unavailable(message: str) -> AppError:
    return AppError("SOURCE_UNAVAILABLE", message, 503)
