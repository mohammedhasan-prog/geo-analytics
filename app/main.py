"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_config import configure_logging
from app.uploads import MAX_UPLOAD_BYTES, UploadRejected, save_upload

configure_logging()
logger = logging.getLogger("geospatial_api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("application_started")
    yield
    logger.info("application_stopped")


app = FastAPI(
    title="Geospatial File Measurement API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    """Report that the API process is ready to serve requests."""
    return {"status": "ok"}


@app.post("/api/v1/measure", status_code=201, tags=["measurements"])
async def upload_for_measurement(request: Request) -> dict[str, str | int]:
    """Accept a KML or zipped Shapefile and retain it for processing."""
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        return JSONResponse(status_code=400, content={"detail": "Expected multipart/form-data with a 'file' field"})

    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_UPLOAD_BYTES + 1024 * 1024:
        return JSONResponse(status_code=400, content={"detail": f"Request exceeds the {MAX_UPLOAD_BYTES}-byte upload limit"})

    try:
        form = await request.form(max_files=1, max_fields=10)
    except Exception:
        logger.warning("malformed_multipart_upload", extra={"path": request.url.path})
        return JSONResponse(status_code=400, content={"detail": "Malformed multipart upload"})

    upload = form.get("file")
    if not hasattr(upload, "read") or not hasattr(upload, "filename"):
        return JSONResponse(status_code=400, content={"detail": "A file field is required"})

    try:
        result = await save_upload(upload)
    except UploadRejected as exc:
        logger.warning("upload_rejected", extra={"reason": str(exc)})
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    logger.info("upload_received", extra={"file_id": result["id"], "size_bytes": result["size_bytes"]})
    return result


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    logger.warning(
        "http_error",
        extra={"path": request.url.path, "status_code": exc.status_code},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning(
        "request_validation_error",
        extra={"path": request.url.path, "errors": exc.errors()},
    )
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_error", extra={"path": request.url.path})
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
