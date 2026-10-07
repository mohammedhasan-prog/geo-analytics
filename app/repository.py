"""Small SQLite repository for completed file summaries and measurements."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "data/geospatial.sqlite3"))


def _connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_repository() -> None:
    with closing(_connect()) as connection:
        with connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS processed_files (
                    id TEXT PRIMARY KEY,
                    info_json TEXT NOT NULL,
                    features_json TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS measurement_jobs (
                    job_id TEXT PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )


def save_result(file_info: dict[str, Any], features: list[dict[str, Any]]) -> None:
    with closing(_connect()) as connection:
        with connection:
            connection.execute(
                """INSERT OR REPLACE INTO processed_files (id, info_json, features_json)
                   VALUES (?, ?, ?)""",
                (
                    file_info["id"],
                    json.dumps(file_info, ensure_ascii=False, default=str),
                    json.dumps(features, ensure_ascii=False, default=str),
                ),
            )


def get_file_info(file_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT info_json FROM processed_files WHERE id = ?", (file_id,)
        ).fetchone()
    return json.loads(row["info_json"]) if row else None


def get_file_features(file_id: str) -> list[dict[str, Any]] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT features_json FROM processed_files WHERE id = ?", (file_id,)
        ).fetchone()
    return json.loads(row["features_json"]) if row else None


def get_processed_file(file_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT info_json, features_json FROM processed_files WHERE id = ?", (file_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "file": json.loads(row["info_json"]),
        "features": json.loads(row["features_json"]),
    }


def create_measurement_job(job_id: str, file_id: str) -> None:
    with closing(_connect()) as connection:
        with connection:
            connection.execute(
                "INSERT INTO measurement_jobs (job_id, file_id, status) VALUES (?, ?, 'QUEUED')",
                (job_id, file_id),
            )


def update_measurement_job(
    job_id: str, status: str, error: str | None = None
) -> None:
    with closing(_connect()) as connection:
        with connection:
            connection.execute(
                """UPDATE measurement_jobs
                   SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE job_id = ?""",
                (status, error, job_id),
            )


def delete_measurement_job(job_id: str) -> None:
    with closing(_connect()) as connection:
        with connection:
            connection.execute("DELETE FROM measurement_jobs WHERE job_id = ?", (job_id,))


def get_measurement_job(job_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            """SELECT job_id, file_id, status, error, created_at, updated_at
               FROM measurement_jobs WHERE job_id = ?""",
            (job_id,),
        ).fetchone()
    return dict(row) if row else None
