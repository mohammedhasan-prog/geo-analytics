"""Minimal structured JSON logging configuration."""

import json
import logging
from datetime import datetime, timezone

_STANDARD_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Format each log record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """Configure one JSON stream handler for application logs."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
