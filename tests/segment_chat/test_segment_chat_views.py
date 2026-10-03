from unittest.mock import AsyncMock, patch

import httpx

from worker_api.segment_chat import segment_chat_views as views
from worker_api.segment_chat.schemas import SegmentChatContext
from worker_api.segment_chat.services.library_client import SegmentNotFoundError
from worker_api.segment_chat.services.llm_client import LLMNotConfiguredError

URL = "/api/v1/segment-chat/stream"
BODY = {"segment_id": "1AIZhR8IBkX4WMfNYkmpc", "question": "What does this verse mean?"}


def _context() -> SegmentChatContext:
    return SegmentChatContext.model_validate({
        "segment": {"segment_id": BODY["segment_id"], "content": "verse", "text": None},
        "sources": [
            {"ref": 1, "type": "commentary", "segment_id": "s1", "text_id": "c1",
             "title": "Commentary", "language": "bo", "content": "explanation"},
        ],
    })


def _patches(*, context=None, context_error=None, rate_limited=False, settings_error=None):
    async def fake_stream(**_kwargs):
        yield "Answer [1]"

    get_context = AsyncMock(return_value=context or _context(), side_effect=context_error)
    settings = (
        patch.object(views.llm_client, "get_llm_settings", side_effect=settings_error)
        if settings_error
        else patch.object(views.llm_client, "get_llm_settings", return_value={})
    )
    return [
        settings,
        patch.object(views, "is_rate_limited", AsyncMock(return_value=rate_limited)),
        patch.object(views, "get_segment_context", get_context),
        patch("worker_api.segment_chat.services.segment_chat_service.llm_client.stream_completion", fake_stream),
    ]


def _post(client, body=None, **kwargs):
    patches = _patches(**kwargs)
    for p in patches:
        p.start()
    try:
        return client.post(URL, json=body or BODY)
    finally:
        for p in patches:
            p.stop()


class TestStreamEndpoint:
    def test_streams_server_sent_events(self, client):
        response = _post(client)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        text = response.text
        assert text.index("event: sources") < text.index("event: delta") < text.index("event: done")
        assert '"cited_refs": [1]' in text

    def test_segment_not_found(self, client):
        response = _post(client, context_error=SegmentNotFoundError("/segments/x"))
        assert response.status_code == 404

    def test_backend_unavailable(self, client):
        response = _post(client, context_error=httpx.ConnectError("down"))
        assert response.status_code == 502

    def test_rate_limited(self, client):
        response = _post(client, rate_limited=True)
        assert response.status_code == 429

    def test_llm_not_configured(self, client):
        response = _post(client, settings_error=LLMNotConfiguredError("no key"))
        assert response.status_code == 503

    def test_disabled(self, client):
        with patch.object(views, "get_bool", return_value=False):
            response = client.post(URL, json=BODY)
        assert response.status_code == 503

    def test_rejects_unsafe_segment_id(self, client):
        response = _post(client, body={**BODY, "segment_id": "../internal"})
        assert response.status_code == 422

    def test_rejects_blank_question(self, client):
        response = _post(client, body={**BODY, "question": "   "})
        assert response.status_code == 422


class TestClientKey:
    def test_prefers_first_forwarded_for_address(self):
        request = type("R", (), {"headers": {"x-forwarded-for": "9.9.9.9, 10.0.0.1"}, "client": None})()
        assert views._client_key(request) == "9.9.9.9"

    def test_falls_back_to_client_host(self):
        client = type("C", (), {"host": "1.2.3.4"})()
        request = type("R", (), {"headers": {}, "client": client})()
        assert views._client_key(request) == "1.2.3.4"
