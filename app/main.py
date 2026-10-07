"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from uuid import uuid4

from app.cache import get_cached_measurement, set_cached_measurement
from app.geoprocessing import ProcessingRejected, process_file
from app.jobs import ASYNC_THRESHOLD_BYTES, enqueue_measurement
from app.logging_config import configure_logging
from app.repository import (
    create_measurement_job,
    delete_measurement_job,
    get_file_info,
    get_file_features,
    get_measurement_job,
    get_processed_file,
    initialize_repository,
    save_result,
)
from app.uploads import MAX_UPLOAD_BYTES, UPLOAD_DIRECTORY, UploadRejected, save_upload

configure_logging()
logger = logging.getLogger("geospatial_api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await run_in_threadpool(initialize_repository)
    logger.info("application_started")
    yield
    logger.info("application_stopped")


app = FastAPI(
    title="Geospatial File Measurement API",
    version="0.1.0",
    lifespan=lifespan,
)

FRONTEND_FILE = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


@app.get("/", include_in_schema=False)
async def frontend() -> FileResponse:
    """Serve the lightweight browser dashboard from the API origin."""
    return FileResponse(FRONTEND_FILE, media_type="text/html")


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    """Report that the API process is ready to serve requests."""
    return {"status": "ok"}


@app.post("/api/v1/measure", status_code=201, tags=["measurements"])
async def upload_for_measurement(request: Request) -> dict[str, Any]:
    """Accept a KML or zipped Shapefile, process it, and retain its results."""
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
        uploaded = await save_upload(upload)
    except UploadRejected as exc:
        logger.warning("upload_rejected", extra={"reason": str(exc)})
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    file_path = UPLOAD_DIRECTORY / f"{uploaded['id']}{Path(uploaded['filename']).suffix.casefold()}"
    cached = await run_in_threadpool(get_cached_measurement, str(uploaded["sha256"]))
    if cached is not None:
        result = {
            "id": uploaded["id"],
            "filename": uploaded["filename"],
            "size_bytes": uploaded["size_bytes"],
            "feature_count": cached["feature_count"],
            "crs": cached["crs"],
            "status": "COMPLETED",
        }
        await run_in_threadpool(save_result, result, cached["features"])
        file_path.unlink(missing_ok=True)
        result["cache_hit"] = True
        logger.info("measurement_cache_hit", extra={"file_id": uploaded["id"]})
        return result

    if int(uploaded["size_bytes"]) > ASYNC_THRESHOLD_BYTES:
        job_id = uuid4().hex
        await run_in_threadpool(create_measurement_job, job_id, str(uploaded["id"]))
        try:
            await run_in_threadpool(
                enqueue_measurement,
                job_id,
                str(uploaded["id"]),
                str(uploaded["filename"]),
                int(uploaded["size_bytes"]),
                str(file_path),
                str(uploaded["sha256"]),
            )
        except Exception:
            await run_in_threadpool(delete_measurement_job, job_id)
            file_path.unlink(missing_ok=True)
            logger.exception("measurement_queue_unavailable", extra={"job_id": job_id})
            return JSONResponse(
                status_code=503,
                content={"detail": "Background processing queue is unavailable"},
            )

        logger.info(
            "measurement_job_queued",
            extra={"job_id": job_id, "file_id": uploaded["id"], "size_bytes": uploaded["size_bytes"]},
        )
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job_id,
                "status": "QUEUED",
                "status_url": f"/api/v1/measure/{job_id}",
            },
        )

    try:
        result = await run_in_threadpool(
            process_file,
            str(uploaded["id"]),
            str(uploaded["filename"]),
            int(uploaded["size_bytes"]),
            file_path,
        )
    except ProcessingRejected as exc:
        file_path.unlink(missing_ok=True)
        logger.warning("upload_processing_rejected", extra={"file_id": uploaded["id"], "reason": str(exc)})
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except Exception:
        file_path.unlink(missing_ok=True)
        raise

    features = await run_in_threadpool(get_file_features, str(uploaded["id"]))
    if features is None:
        raise RuntimeError("Processed measurement result was not persisted")
    await run_in_threadpool(
        set_cached_measurement,
        str(uploaded["sha256"]),
        {"feature_count": result["feature_count"], "crs": result["crs"], "features": features},
    )
    logger.info("upload_processed", extra={"file_id": result["id"], "feature_count": result["feature_count"]})
    return result


@app.get("/api/v1/measure/{job_id}", tags=["measurements"])
async def measurement_job_status(job_id: str) -> dict[str, Any]:
    """Poll a large-file measurement job and return its final results."""
    job = await run_in_threadpool(get_measurement_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Measurement job not found")

    response: dict[str, Any] = {
        "job_id": job["job_id"],
        "file_id": job["file_id"],
        "status": job["status"],
    }
    if job["status"] == "FAILED":
        response["error"] = job["error"] or "Measurement processing failed"
    elif job["status"] == "COMPLETED":
        result = await run_in_threadpool(get_processed_file, job["file_id"])
        response["result"] = result
    return response


@app.get("/api/files/{file_id}/", tags=["files"])
async def file_information(file_id: str) -> dict[str, Any]:
    """Return the persisted summary for a processed upload."""
    info = await run_in_threadpool(get_file_info, file_id)
    if info is None:
        raise HTTPException(status_code=404, detail="File not found")
    return info


@app.get("/api/files/{file_id}/measurements/", tags=["measurements"])
async def file_measurements(file_id: str) -> dict[str, Any]:
    """Return extracted features and measurements for a processed upload."""
    features = await run_in_threadpool(get_file_features, file_id)
    if features is None:
        raise HTTPException(status_code=404, detail="File not found")
    info = await run_in_threadpool(get_file_info, file_id)
    return {
        "id": file_id,
        "feature_count": len(features),
        "crs": info["crs"] if info else None,
        "features": features,
    }


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
