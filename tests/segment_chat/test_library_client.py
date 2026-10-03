import json
import os
from unittest.mock import patch

import httpx
import pytest

from worker_api.segment_chat.services import library_client
from worker_api.segment_chat.services.library_client import LibraryClient, SegmentNotFoundError

BASE_ENV = {
    "OPENPECHA_LIBRARY_URL": "https://library.test/",
    "OPENPECHA_APP_NAME": "webuddhist",
    "OPENPECHA_API_KEY": "",
    "OPENPECHA_TIMEOUT_SECONDS": "15",
    "OPENPECHA_MAX_CONCURRENCY": "4",
}


def _client(handler, **env) -> LibraryClient:
    """A LibraryClient whose HTTP calls go to `handler` instead of the network."""
    with patch.dict(os.environ, {**BASE_ENV, **env}):
        client = LibraryClient()
    transport = httpx.MockTransport(handler)
    client._http = httpx.AsyncClient(
        base_url=str(client._http.base_url),
        headers=client._http.headers,
        transport=transport,
    )
    return client


def _json(payload, status_code=200) -> httpx.Response:
    return httpx.Response(status_code, content=json.dumps(payload).encode(), headers={"content-type": "application/json"})


class TestConfig:
    def test_headers_without_api_key(self):
        with patch.dict(os.environ, BASE_ENV):
            assert library_client._headers() == {"X-Application": "webuddhist"}

    def test_headers_with_api_key(self):
        with patch.dict(os.environ, {**BASE_ENV, "OPENPECHA_API_KEY": "key"}):
            assert library_client._headers() == {"X-Application": "webuddhist", "X-API-Key": "key"}

    def test_url_is_required(self):
        with patch.dict(os.environ, {**BASE_ENV, "OPENPECHA_LIBRARY_URL": " "}):
            with pytest.raises(RuntimeError, match="OPENPECHA_LIBRARY_URL"):
                library_client._library_url()

    @pytest.mark.asyncio
    async def test_client_uses_config(self):
        with patch.dict(os.environ, {**BASE_ENV, "OPENPECHA_API_KEY": "key"}):
            async with LibraryClient() as client:
                assert str(client._http.base_url) == "https://library.test"
                assert client._http.headers["X-API-Key"] == "key"
                assert client._http.timeout.read == 15.0


class TestRequests:
    @pytest.mark.asyncio
    async def test_get_segment_sends_app_header(self):
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["app"] = request.headers.get("X-Application")
            return _json({"id": "s1", "text_id": "t1"})

        async with _client(handler) as client:
            assert await client.get_segment("s1") == {"id": "s1", "text_id": "t1"}
        assert seen == {"path": "/v2/segments/s1", "app": "webuddhist"}

    @pytest.mark.asyncio
    async def test_get_segment_not_found(self):
        async with _client(lambda request: _json({"error": "nope"}, 404)) as client:
            with pytest.raises(SegmentNotFoundError):
                await client.get_segment("missing")

    @pytest.mark.asyncio
    async def test_get_segment_server_error(self):
        async with _client(lambda request: _json({}, 500)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.get_segment("s1")

    @pytest.mark.asyncio
    async def test_get_related_page_passes_paging(self):
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["params"] = dict(request.url.params)
            return _json({"items": [], "has_more": False})

        async with _client(handler) as client:
            await client.get_related_page("s1", offset=100, limit=50)
        assert seen == {"path": "/v2/segments/s1/related", "params": {"offset": "100", "limit": "50"}}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload, expected",
        [("plain text", "plain text"), ({"content": "dict text"}, "dict text"), ({"value": "v"}, "v"), (42, None)],
    )
    async def test_get_segment_content_shapes(self, payload, expected):
        async with _client(lambda request: _json(payload)) as client:
            assert await client.get_segment_content("s1") == expected

    @pytest.mark.asyncio
    async def test_get_segment_content_failure_is_none(self):
        async with _client(lambda request: _json({}, 500)) as client:
            assert await client.get_segment_content("s1") is None

    @pytest.mark.asyncio
    async def test_get_text(self):
        async with _client(lambda request: _json({"id": "t1", "language": "bo"})) as client:
            assert await client.get_text("t1") == {"id": "t1", "language": "bo"}

    @pytest.mark.asyncio
    async def test_get_text_failure_is_none(self):
        async with _client(lambda request: _json({}, 404)) as client:
            assert await client.get_text("t1") is None

    @pytest.mark.asyncio
    async def test_get_text_source_link(self):
        seen = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return _json([{"source": "https://dharmamitra.org"}])

        async with _client(handler) as client:
            assert await client.get_text_source_link("t1") == "https://dharmamitra.org"
        assert seen["params"] == {"edition_type": "critical"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("response", [_json([]), _json({}, 500)])
    async def test_get_text_source_link_missing(self, response):
        async with _client(lambda request: response) as client:
            assert await client.get_text_source_link("t1") is None


class TestRetries:
    @pytest.mark.asyncio
    async def test_retries_transport_errors(self):
        calls = {"count": 0}

        def handler(request):
            calls["count"] += 1
            if calls["count"] < 3:
                raise httpx.ConnectError("reset", request=request)
            return _json({"id": "s1"})

        with patch.object(library_client.asyncio, "sleep"):
            async with _client(handler) as client:
                assert await client.get_segment("s1") == {"id": "s1"}
        assert calls["count"] == 3

    @pytest.mark.asyncio
    async def test_gives_up_after_three_attempts(self):
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        with patch.object(library_client.asyncio, "sleep"):
            async with _client(handler) as client:
                with pytest.raises(httpx.ReadTimeout):
                    await client.get_segment("s1")
