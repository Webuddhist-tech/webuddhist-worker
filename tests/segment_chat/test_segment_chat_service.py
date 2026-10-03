import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from worker_api.segment_chat.schemas import SegmentChatContext, SegmentChatRequest
from worker_api.segment_chat.services import chat_prompt
from worker_api.segment_chat.services import segment_chat_service as service

SEGMENT_ID = "seg1"


def _context(sources=None) -> SegmentChatContext:
    return SegmentChatContext.model_validate({
        "segment": {
            "segment_id": SEGMENT_ID,
            "content": "root verse",
            "text": {"text_id": "root", "title": "Root Text", "language": "bo"},
        },
        "sources": sources if sources is not None else [
            {"ref": 1, "type": "translation", "segment_id": "s1", "text_id": "t1",
             "title": "English", "language": "en", "content": "A translation"},
            {"ref": 2, "type": "commentary", "segment_id": "s2", "text_id": "c1",
             "title": "Commentary", "language": "bo", "content": "x" * 400, "truncated": True},
        ],
    })


def _parse_events(chunks: list[str]) -> list[tuple[str, dict]]:
    events = []
    for chunk in chunks:
        event_line, data_line = chunk.strip().split("\n")
        events.append((event_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))))
    return events


async def _collect(generator) -> list[str]:
    return [chunk async for chunk in generator]


class TestCitedRefs:
    def test_returns_valid_refs_in_first_use_order(self):
        assert service.cited_refs("a [2] b [1][2] c [9]", {1, 2}) == [2, 1]

    def test_no_citations(self):
        assert service.cited_refs("plain answer", {1}) == []


class TestStreamSegmentChat:
    @pytest.mark.asyncio
    async def test_emits_sources_deltas_and_done(self):
        async def fake_stream(**kwargs):
            assert "## Question\nWhat does it mean?" in kwargs["user_message"]
            for text in ["It means ", "kindness [2]."]:
                yield text

        request = SegmentChatRequest(segment_id=SEGMENT_ID, question="What does it mean?")
        with patch.object(service.llm_client, "stream_completion", fake_stream):
            events = _parse_events(await _collect(service.stream_segment_chat(request, _context())))

        assert [name for name, _ in events] == ["sources", "delta", "delta", "done"]
        sources_payload = events[0][1]
        assert sources_payload["segment"]["segment_id"] == SEGMENT_ID
        assert [s["ref"] for s in sources_payload["sources"]] == [1, 2]
        assert "content" not in sources_payload["sources"][0]
        assert sources_payload["sources"][1]["snippet"].endswith("…")
        assert events[2][1] == {"text": "kindness [2]."}
        assert events[3][1] == {"cited_refs": [2]}

    @pytest.mark.asyncio
    async def test_emits_error_when_llm_fails(self):
        async def failing_stream(**_kwargs):
            yield "partial"
            raise RuntimeError("quota")

        request = SegmentChatRequest(segment_id=SEGMENT_ID, question="Why?")
        with patch.object(service.llm_client, "stream_completion", failing_stream):
            events = _parse_events(await _collect(service.stream_segment_chat(request, _context())))

        assert [name for name, _ in events] == ["sources", "delta", "error"]
        assert "message" in events[2][1]


class TestRateLimit:
    def _config(self, limit):
        values = {"SEGMENT_CHAT_RATE_LIMIT_PER_MINUTE": str(limit), "SEGMENT_CHAT_RATE_LIMIT_KEY_PREFIX": "rl:"}
        return patch.dict(os.environ, values)

    @pytest.mark.asyncio
    async def test_first_request_sets_expiry(self):
        redis_client = AsyncMock()
        redis_client.incr.return_value = 1
        with self._config(2), patch.object(service, "get_redis_client", return_value=redis_client):
            assert await service.is_rate_limited("1.2.3.4") is False
        redis_client.expire.assert_awaited_once_with("rl:1.2.3.4", 60)

    @pytest.mark.asyncio
    async def test_over_limit(self):
        redis_client = AsyncMock()
        redis_client.incr.return_value = 3
        with self._config(2), patch.object(service, "get_redis_client", return_value=redis_client):
            assert await service.is_rate_limited("1.2.3.4") is True

    @pytest.mark.asyncio
    async def test_disabled(self):
        with self._config(0), patch.object(service, "get_redis_client") as get_client:
            assert await service.is_rate_limited("1.2.3.4") is False
        get_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_fails_open_without_redis(self):
        redis_client = AsyncMock()
        redis_client.incr.side_effect = ConnectionError("down")
        with self._config(2), patch.object(service, "get_redis_client", return_value=redis_client):
            assert await service.is_rate_limited("1.2.3.4") is False


class TestChatPrompt:
    def test_language_rule(self):
        assert "code 'bo'" in chat_prompt.build_system_prompt("bo")
        assert "same language as the user's question" in chat_prompt.build_system_prompt(None)

    def test_context_block_lists_numbered_sources(self):
        block = chat_prompt.build_context_block(_context())
        assert block.startswith("## Selected segment (from: Root Text, bo)\nroot verse")
        assert "[1] Translation — English, en\nA translation" in block
        assert "[2] Commentary — Commentary, bo (excerpt)" in block

    def test_context_block_without_sources(self):
        block = chat_prompt.build_context_block(_context(sources=[]))
        assert "No related texts are available" in block
