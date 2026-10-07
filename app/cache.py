"""Best-effort Redis cache for content-addressed measurement results."""

import json
import logging
import os
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger("geospatial_api.cache")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", str(24 * 60 * 60)))
KEY_PREFIX = "geospatial:measurement:v1:"


def get_cached_measurement(sha256: str) -> dict[str, Any] | None:
    """Return cached CRS, feature summary, and features, or None on a miss/error."""
    connection = Redis.from_url(REDIS_URL, socket_connect_timeout=0.25, socket_timeout=0.5)
    try:
        value = connection.get(KEY_PREFIX + sha256)
        if value is None:
            return None
        decoded = json.loads(value)
        if not isinstance(decoded, dict) or not isinstance(decoded.get("features"), list):
            logger.warning("invalid_measurement_cache_entry", extra={"sha256": sha256})
            return None
        return decoded
    except (RedisError, ValueError, TypeError):
        logger.warning("measurement_cache_read_failed", extra={"sha256": sha256}, exc_info=True)
        return None
    finally:
        connection.close()


def set_cached_measurement(sha256: str, value: dict[str, Any]) -> None:
    """Store a canonical processed result for the configured TTL."""
    connection = Redis.from_url(REDIS_URL, socket_connect_timeout=0.25, socket_timeout=0.5)
    try:
        connection.set(
            KEY_PREFIX + sha256,
            json.dumps(value, ensure_ascii=False, allow_nan=True, default=str),
            ex=CACHE_TTL_SECONDS,
        )
    except (RedisError, ValueError, TypeError):
        logger.warning("measurement_cache_write_failed", extra={"sha256": sha256}, exc_info=True)
    finally:
        connection.close()
