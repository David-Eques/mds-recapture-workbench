"""Public FastAPI application for the local MDS recapture workbench."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .capabilities import collect_capabilities
from .capabilities import router as capabilities_router
from .workbench import router as workbench_router

app = FastAPI(
    title="MDS Recapture Workbench",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(workbench_router)
app.include_router(capabilities_router)


@app.exception_handler(RequestValidationError)
def validation_error(request: Request, _exc: RequestValidationError) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or uuid4().hex
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "invalid_request",
                "message": "the multipart request does not match the analysis contract",
                "request_id": request_id,
            }
        },
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", response_model=None)
def readyz(request: Request) -> dict | JSONResponse:
    capabilities = collect_capabilities()
    if capabilities["ready"]:
        return {"ready": True, "versions": capabilities["runtime"]}
    request_id = request.headers.get("x-request-id") or uuid4().hex
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "processing_dependency_unavailable",
                "message": "one or more processing dependencies are unavailable",
                "request_id": request_id,
            }
        },
    )
