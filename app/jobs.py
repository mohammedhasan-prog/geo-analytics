"""Redis Queue integration for large measurement uploads."""

import logging
import os
from pathlib import Path
from typing import Any

from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer

from app.geoprocessing import process_file
from app.repository import update_measurement_job

logger = logging.getLogger("geospatial_api.jobs")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
JOB_QUEUE_NAME = "geospatial-measurements"
ASYNC_THRESHOLD_BYTES = int(os.getenv("ASYNC_THRESHOLD_BYTES", str(5 * 1024 * 1024)))


def enqueue_measurement(
    job_id: str, file_id: str, filename: str, size_bytes: int, file_path: str
) -> None:
    """Push a measurement task to Redis Queue."""
    connection = Redis.from_url(REDIS_URL, socket_connect_timeout=3, socket_timeout=5)
    try:
        connection.ping()
        queue = Queue(JOB_QUEUE_NAME, connection=connection, serializer=JSONSerializer)
        queue.enqueue(
            run_measurement_job,
            job_id,
            file_id,
            filename,
            size_bytes,
            file_path,
            job_id=job_id,
            job_timeout=60 * 60,
            result_ttl=24 * 60 * 60,
            failure_ttl=7 * 24 * 60 * 60,
        )
    finally:
        connection.close()


def run_measurement_job(
    job_id: str, file_id: str, filename: str, size_bytes: int, file_path: str
) -> dict[str, Any]:
    """Run a queued measurement and persist its final job state."""
    update_measurement_job(job_id, "RUNNING")
    try:
        result = process_file(file_id, filename, size_bytes, Path(file_path))
    except Exception:
        update_measurement_job(job_id, "FAILED", "Measurement processing failed")
        logger.exception("background_measurement_failed", extra={"job_id": job_id})
        raise

    update_measurement_job(job_id, "COMPLETED")
    logger.info("background_measurement_completed", extra={"job_id": job_id, "file_id": file_id})
    return result
