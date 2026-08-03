"""One error shape for every failure, including validation.

FastAPI's defaults would give three different shapes — its own `{"detail": …}`
for HTTPException, a list-of-dicts for request validation, and an HTML 500 page
for anything unhandled. A client would need three parsers, and the third would
leak a stack trace. Everything here is normalised to:

    {"error": {"code": …, "message": …, "detail": {…}}}

`code` is the stable contract. Clients branch on it; `message` is prose and may
be reworded without notice.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ApiError(Exception):
    """Base for every deliberate API failure.

    Carries its own HTTP status so raising one at any depth produces the right
    response without the handler knowing which error it caught.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, detail: Optional[Dict[str, Any]] = None,
                 code: Optional[str] = None, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code

    def to_payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message,
                          "detail": self.detail}}


class InvalidRequest(ApiError):
    status_code = 400
    code = "invalid_request"


class InvalidId(ApiError):
    status_code = 400
    code = "invalid_id"


class NotFound(ApiError):
    status_code = 404
    code = "not_found"


class ProjectNotFound(NotFound):
    code = "project_not_found"

    def __init__(self, project_id: str):
        super().__init__(f"No project with id {project_id}",
                         detail={"project_id": project_id})


class RunNotFound(NotFound):
    code = "run_not_found"

    def __init__(self, run_id: str):
        super().__init__(f"No run with id {run_id}", detail={"run_id": run_id})


class TimelineNotFound(NotFound):
    code = "timeline_not_found"

    def __init__(self, project_id: str, version: Optional[int] = None):
        super().__init__(
            f"Project {project_id} has no timeline"
            + (f" at version {version}" if version else " yet"),
            detail={"project_id": project_id, "version": version},
        )


class QaNotFound(NotFound):
    code = "qa_not_found"

    def __init__(self, project_id: str):
        super().__init__(
            f"Project {project_id} has no QA report yet",
            detail={"project_id": project_id},
        )


class AudioNotFound(NotFound):
    """Authorised audio key, but the WAV is not in storage."""

    code = "audio_not_found"

    def __init__(self, key: str):
        super().__init__(
            f"No audio object at {key}",
            detail={"key": key},
        )


class MixNotFound(NotFound):
    """Project or run exists, but there is no completed mix to serve."""

    code = "mix_not_found"

    def __init__(self, project_id: str, run_id: Optional[str] = None):
        if run_id:
            super().__init__(
                f"Run {run_id} has no completed mix",
                detail={"project_id": project_id, "run_id": run_id},
            )
        else:
            super().__init__(
                "Project has no completed mix yet",
                detail={"project_id": project_id},
            )


class AlreadyRunning(ApiError):
    status_code = 409
    code = "already_running"

    def __init__(self, project_id: str, run_id: str, kind: str):
        super().__init__(
            f"A {kind} run is already in flight for this project",
            detail={"project_id": project_id, "run_id": run_id, "kind": kind},
        )


class ImmutableConflict(ApiError):
    status_code = 409
    code = "immutable_conflict"


class AlreadyClaimed(ApiError):
    """The orphan is not an orphan any more (unit 7.17): a project record
    already exists for the prefix, or the ledger rows were already rescued."""

    status_code = 409
    code = "already_claimed"


class PreconditionFailed(ApiError):
    """Well-formed request, wrong state — e.g. export before analysis."""

    status_code = 422
    code = "precondition_failed"


class QuotaExhausted(ApiError):
    """First-class, not a generic 429.

    The Gemini free tier allows 20 analysis requests per day, so "out of budget
    until tomorrow" is a normal operating state a client must be able to
    distinguish from a transient failure — and it needs the reset time to say
    anything useful to the user.
    """

    status_code = 429
    code = "quota_exhausted"

    def __init__(self, model_id: str, resets_at: Optional[str] = None,
                 used: Optional[int] = None, limit: Optional[int] = None):
        super().__init__(
            f"Daily request budget for {model_id} is spent",
            detail={"model_id": model_id, "resets_at": resets_at,
                    "used": used, "limit": limit, "estimate": True},
        )


class SpendCapReached(ApiError):
    """The paid SFX tier's daily dollar cap is spent (a hard stop, unlike the
    advisory project budget).

    Measured from the ElevenLabs workspace analytics (the real bill, not the
    ledger's estimates); ``resets_at`` is the next UTC midnight. The demo
    must never silently run out of credits, so once the cap is crossed the
    billable entry points refuse until it resets — free reads and the
    advisory budget are unaffected.
    """

    status_code = 429
    code = "spend_cap_reached"

    def __init__(self, model_id: str, *, cap_usd: Optional[float] = None,
                 spend_usd: Optional[float] = None,
                 resets_at: Optional[str] = None):
        when = (f" (resets {resets_at})" if resets_at else "")
        super().__init__(
            f"Daily spend cap for {model_id} is reached{when}",
            detail={"model_id": model_id, "cap_usd": cap_usd,
                    "spend_usd": spend_usd, "resets_at": resets_at},
        )


class ProviderError(ApiError):
    status_code = 502
    code = "provider_error"


class StorageUnavailable(ApiError):
    status_code = 503
    code = "storage_unavailable"


def install_handlers(app) -> None:
    """Register handlers so every failure leaves through the same shape."""
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(ApiError)
    def _api_error(_request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    def _validation_error(_request: Request, exc: RequestValidationError):
        # Flattened into the standard shape. FastAPI's native list-of-dicts
        # would be the one response a client had to special-case.
        return JSONResponse(
            status_code=400,
            content=InvalidRequest(
                "Request body or parameters failed validation",
                detail={"errors": [
                    {"field": ".".join(str(p) for p in e.get("loc", [])),
                     "problem": e.get("msg", "")}
                    for e in exc.errors()
                ]},
            ).to_payload(),
        )

    @app.exception_handler(StarletteHTTPException)
    def _http_error(_request: Request, exc: StarletteHTTPException):
        codes = {404: "not_found", 405: "method_not_allowed"}
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiError(str(exc.detail),
                             code=codes.get(exc.status_code, "http_error"),
                             status_code=exc.status_code).to_payload(),
        )

    @app.exception_handler(Exception)
    def _unhandled(_request: Request, exc: Exception):
        # The message is deliberately generic: an unhandled exception's text can
        # contain a signed URL, a bucket name, or a key fragment, and this is
        # the one path where nobody chose what the client sees. The real error
        # goes to the log.
        import logging
        logging.getLogger("foleyflow.api").exception("unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=ApiError("An unexpected error occurred").to_payload(),
        )
