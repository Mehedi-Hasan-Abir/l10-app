"""Application errors. Error body: `{"detail": {"code": "snake_case", "message": "...", ...extra}}`."""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}

    def body(self) -> dict[str, Any]:
        return {"detail": {"code": self.code, "message": self.message, **self.extra}}


async def app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(status_code=exc.status, content=exc.body())


# Helpers return the exception, so call sites read `raise forbidden("team_forbidden", "...")`.


def unauthorized(
    code: str = "not_authenticated", message: str = "Authentication required", **extra: Any
) -> AppError:
    return AppError(401, code, message, extra)


def forbidden(
    code: str = "forbidden", message: str = "You do not have access to this resource", **extra: Any
) -> AppError:
    return AppError(403, code, message, extra)


def not_found(code: str = "not_found", message: str = "Resource not found", **extra: Any) -> AppError:
    return AppError(404, code, message, extra)


def conflict(
    code: str = "conflict", message: str = "The request conflicts with the current state", **extra: Any
) -> AppError:
    return AppError(409, code, message, extra)


def unprocessable(
    code: str = "unprocessable", message: str = "The request could not be processed", **extra: Any
) -> AppError:
    return AppError(422, code, message, extra)
