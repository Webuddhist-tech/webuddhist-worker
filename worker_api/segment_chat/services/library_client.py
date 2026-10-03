"""OpenPecha library API (the source of segments, texts and their relations)."""
import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from worker_api.config import get, get_float, get_int

logger = logging.getLogger(__name__)

# Idempotent GETs that left us with no response at all, so replaying is safe.
_RETRYABLE_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)
_ATTEMPTS = 3


class SegmentNotFoundError(Exception):
    """The library has no segment with the requested id."""


def _library_url() -> str:
    library_url = get("OPENPECHA_LIBRARY_URL").strip().rstrip("/")
    if not library_url:
        raise RuntimeError("OPENPECHA_LIBRARY_URL is not configured")
    return library_url


def _headers() -> Dict[str, str]:
    headers = {"X-Application": get("OPENPECHA_APP_NAME").strip() or "webuddhist"}
    api_key = get("OPENPECHA_API_KEY").strip()
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


class LibraryClient:
    """One client per context build: a shared connection pool, and a gate so
    fanning out over every related segment never floods the library."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=_library_url(),
            headers=_headers(),
            timeout=httpx.Timeout(get_float("OPENPECHA_TIMEOUT_SECONDS"), connect=5.0),
            follow_redirects=True,
        )
        self._gate = asyncio.Semaphore(max(get_int("OPENPECHA_MAX_CONCURRENCY"), 1))

    async def __aenter__(self) -> "LibraryClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> httpx.Response:
        for attempt in range(_ATTEMPTS):
            try:
                async with self._gate:
                    return await self._http.get(path, params=params)
            except _RETRYABLE_ERRORS as error:
                if attempt == _ATTEMPTS - 1:
                    raise
                logger.warning(
                    "Library GET %s failed (%s), retrying %d/%d",
                    path,
                    type(error).__name__,
                    attempt + 1,
                    _ATTEMPTS - 1,
                )
                await asyncio.sleep(0.2 * 2**attempt)
        raise AssertionError("unreachable")  # pragma: no cover

    async def get_segment(self, segment_id: str) -> Dict[str, Any]:
        """GET /v2/segments/{id} -> {id, text_id, edition_id, type, reference, lines}"""
        response = await self._get(f"/v2/segments/{segment_id}")
        if response.status_code == 404:
            raise SegmentNotFoundError(segment_id)
        response.raise_for_status()
        return response.json()

    async def get_related_page(self, segment_id: str, *, offset: int, limit: int) -> Dict[str, Any]:
        """GET /v2/segments/{id}/related -> {items: [{id, text_id, ...}], has_more}

        Related segments of every kind: translations, commentaries and the
        root text, each identified by its own segment id and text_id.
        """
        response = await self._get(
            f"/v2/segments/{segment_id}/related",
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return response.json()

    async def get_segment_content(self, segment_id: str) -> Optional[str]:
        """Content of one segment, or None if it could not be read."""
        try:
            response = await self._get(f"/v2/segments/{segment_id}/content")
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("Could not read content of segment %s", segment_id, exc_info=True)
            return None
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("content", "text", "value"):
                if isinstance(data.get(key), str):
                    return data[key]
        return None

    async def get_text(self, text_id: str) -> Optional[Dict[str, Any]]:
        """GET /v2/texts/{id} -> {id, title: {lang: str}, language, translation_of, commentary_of, license}"""
        try:
            response = await self._get(f"/v2/texts/{text_id}")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("Could not read text %s", text_id, exc_info=True)
            return None

    async def get_text_source_link(self, text_id: str) -> Optional[str]:
        """Source of the text's critical edition, if it has one."""
        try:
            response = await self._get(
                f"/v2/texts/{text_id}/editions", params={"edition_type": "critical"}
            )
            response.raise_for_status()
            editions = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        if isinstance(editions, list) and editions and isinstance(editions[0], dict):
            return editions[0].get("source")
        return None
