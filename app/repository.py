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
