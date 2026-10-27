"""Shared 30-day cache for external HTTP API responses.

Redis is the fast first-level cache. PostgreSQL is the durable source of truth.
If either service is temporarily unavailable, API requests continue normally and
the cache retries after a short backoff.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Mapping, Optional

logger = logging.getLogger("api_cache")

CACHE_TTL_SECONDS = int(os.environ.get("API_CACHE_TTL_SECONDS", str(30 * 24 * 60 * 60)))
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://mailbox:mailbox_cache@127.0.0.1:15435/mailbox_cache"
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:16379/0")
_SECRET_QUERY_KEYS = {"key", "api_key", "apikey", "token", "access_token"}


def _request_key(service: str, url: str) -> str:
    """Build a stable fingerprint without retaining credentials in the cache."""
    parsed = urllib.parse.urlsplit(url)
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _SECRET_QUERY_KEYS
    ]
    safe_url = urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, urllib.parse.urlencode(sorted(query)), "")
    )
    digest = hashlib.sha256(f"GET\n{service}\n{safe_url}".encode("utf-8")).hexdigest()
    return f"mailbox:api-cache:v1:{digest}"


class ApiResponseCache:
    def __init__(self) -> None:
        self._redis = None
        self._schema_ready = False
        self._lock = threading.Lock()
        self._retry_after = 0.0

    def _redis_client(self):
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(
                REDIS_URL,
                socket_connect_timeout=0.5,
                socket_timeout=1.0,
                decode_responses=False,
            )
        return self._redis

    def _connect_postgres(self):
        import psycopg

        return psycopg.connect(DATABASE_URL, connect_timeout=1)

    def _ensure_schema(self, connection) -> None:
        if self._schema_ready:
            return
        with self._lock:
            if self._schema_ready:
                return
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS api_response_cache (
                        cache_key TEXT PRIMARY KEY,
                        service TEXT NOT NULL,
                        response_body BYTEA NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        expires_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS api_response_cache_expires_idx "
                    "ON api_response_cache (expires_at)"
                )
            connection.commit()
            self._schema_ready = True

    def get(self, service: str, url: str) -> Optional[bytes]:
        key = _request_key(service, url)
        try:
            value = self._redis_client().get(key)
            if value is not None:
                logger.info("API cache hit (Redis): %s", service)
                return bytes(value)
        except Exception as exc:
            logger.debug("Redis cache read unavailable: %s", exc)

        if time.monotonic() < self._retry_after:
            return None

        try:
            with self._connect_postgres() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT response_body FROM api_response_cache "
                        "WHERE cache_key = %s AND expires_at > NOW()",
                        (key,),
                    )
                    row = cursor.fetchone()
            if row is None:
                return None
            body = bytes(row[0])
            try:
                self._redis_client().setex(key, CACHE_TTL_SECONDS, body)
            except Exception:
                pass
            logger.info("API cache hit (PostgreSQL): %s", service)
            return body
        except Exception as exc:
            logger.warning("API cache unavailable; using live APIs: %s", exc)
            self._retry_after = time.monotonic() + 30.0
            return None

    def set(self, service: str, url: str, body: bytes) -> None:
        key = _request_key(service, url)
        postgres_saved = False
        try:
            with self._connect_postgres() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO api_response_cache
                            (cache_key, service, response_body, created_at, expires_at)
                        VALUES (%s, %s, %s, NOW(), NOW() + (%s * INTERVAL '1 second'))
                        ON CONFLICT (cache_key) DO UPDATE SET
                            service = EXCLUDED.service,
                            response_body = EXCLUDED.response_body,
                            created_at = NOW(),
                            expires_at = EXCLUDED.expires_at
                        """,
                        (key, service, body, CACHE_TTL_SECONDS),
                    )
                connection.commit()
            postgres_saved = True
        except Exception as exc:
            logger.warning("Could not persist API response in PostgreSQL: %s", exc)
            self._retry_after = time.monotonic() + 30.0

        if postgres_saved:
            try:
                self._redis_client().setex(key, CACHE_TTL_SECONDS, body)
            except Exception as exc:
                logger.debug("Could not warm Redis API cache: %s", exc)


_CACHE = ApiResponseCache()


def cached_urlopen_bytes(
    url: str,
    *,
    service: str,
    timeout: float,
    headers: Optional[Mapping[str, str]] = None,
    use_cache: bool = True,
) -> bytes:
    """Return a cached response when enabled, otherwise call the API directly.

    ``use_cache=False`` bypasses both cache reads and cache writes. This makes
    the per-job Flow Builder switch deterministic and safe for concurrent jobs.
    """
    if use_cache:
        cached = _CACHE.get(service, url)
        if cached is not None:
            return cached

    request = urllib.request.Request(url, headers=dict(headers or {}))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    if use_cache:
        _CACHE.set(service, url, body)
    return body
