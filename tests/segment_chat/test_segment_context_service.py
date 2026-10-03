import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from worker_api.segment_chat.schemas import SegmentChatContext, SourceType
from worker_api.segment_chat.services import segment_context_service as service
from worker_api.segment_chat.services.library_client import SegmentNotFoundError

SEGMENT_ID = "selSeg"
ROOT_ID = "rootText"


def _config(**overrides):
    values = {
        "SEGMENT_CHAT_RELATED_PAGE_SIZE": "100",
        "SEGMENT_CHAT_MAX_RELATED_PAGES": "10",
        "SEGMENT_CHAT_MAX_SOURCES": "80",
        "SEGMENT_CHAT_MAX_CONTEXT_CHARS": "60000",
        "SEGMENT_CHAT_MAX_SOURCE_CHARS": "8000",
        "SEGMENT_CHAT_CONTEXT_CACHE_TTL_SECONDS": "600",
        "SEGMENT_CHAT_CONTEXT_CACHE_KEY_PREFIX": "test:ctx:",
        "CACHE_CONNECTION_STRING": "redis://localhost:6379",
    }
    values.update({key: str(value) for key, value in overrides.items()})
    return patch.dict(os.environ, values)


class FakeLibrary:
    """In-memory stand-in for LibraryClient."""

    def __init__(self, *, segment, related_pages, texts, contents, links=None):
        self.segment = segment
        self.related_pages = list(related_pages)
        self.texts = texts
        self.contents = contents
        self.links = links or {}
        self.related_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def get_segment(self, segment_id):
        if isinstance(self.segment, Exception):
            raise self.segment
        return self.segment

    async def get_related_page(self, segment_id, *, offset, limit):
        self.related_calls.append((offset, limit))
        return self.related_pages.pop(0)

    async def get_segment_content(self, segment_id):
        return self.contents.get(segment_id)

    async def get_text(self, text_id):
        return self.texts.get(text_id)

    async def get_text_source_link(self, text_id):
        return self.links.get(text_id)


def _item(segment_id, text_id):
    return {"id": segment_id, "text_id": text_id, "type": "verse"}


class TestHelpers:
    def test_clean_content_strips_tags_and_unescapes(self):
        assert service.clean_content("<p>Hello&nbsp;<b>world</b></p>") == "Hello\xa0world"

    def test_clean_content_br_becomes_newline(self):
        assert service.clean_content("line one<br/>line two") == "line one\nline two"

    def test_clean_content_empty_values(self):
        assert service.clean_content(None) == ""
        assert service.clean_content("") == ""

    def test_clean_content_collapses_blank_lines(self):
        assert service.clean_content("a\n\n\n\nb") == "a\n\nb"

    def test_extract_title(self):
        assert service.extract_title({"bo": "", "en": " Title "}) == "Title"
        assert service.extract_title("Plain") == "Plain"
        assert service.extract_title(None) == ""
        assert service.extract_title({}) == ""

    @pytest.mark.parametrize(
        "related, related_id, selected, expected",
        [
            ({"translation_of": "x"}, "t1", None, SourceType.TRANSLATION),
            ({"commentary_of": "x"}, "c1", None, SourceType.COMMENTARY),
            ({}, ROOT_ID, {"commentary_of": ROOT_ID}, SourceType.ROOT_TEXT),
            ({}, ROOT_ID, {"translation_of": ROOT_ID}, SourceType.ROOT_TEXT),
            ({"id": "other"}, "other", {"commentary_of": ROOT_ID}, None),
            (None, "t1", None, None),
        ],
    )
    def test_classify(self, related, related_id, selected, expected):
        assert service.classify(related, related_id, selected) == expected


class TestFetchRelatedItems:
    @pytest.mark.asyncio
    async def test_pages_until_has_more_false_and_dedupes(self):
        library = FakeLibrary(
            segment={},
            related_pages=[
                {"items": [_item("s1", "t1"), _item("s2", "t1")], "has_more": True},
                {"items": [_item("s2", "t1"), _item("s3", "t2"), {"id": "noText"}], "has_more": False},
            ],
            texts={},
            contents={},
        )
        with _config():
            items = await service._fetch_related_items(library, SEGMENT_ID)

        assert [item["id"] for item in items] == ["s1", "s2", "s3"]
        assert library.related_calls == [(0, 100), (100, 100)]

    @pytest.mark.asyncio
    async def test_stops_at_max_pages_and_caps_page_size(self):
        library = FakeLibrary(
            segment={},
            related_pages=[{"items": [], "has_more": True}] * 3,
            texts={},
            contents={},
        )
        with _config(SEGMENT_CHAT_MAX_RELATED_PAGES=2, SEGMENT_CHAT_RELATED_PAGE_SIZE=500):
            await service._fetch_related_items(library, SEGMENT_ID)

        assert library.related_calls == [(0, 100), (100, 100)]


class TestBuildSources:
    def _row(self, segment_id, content, source_type=SourceType.COMMENTARY):
        return {"type": source_type, "segment_id": segment_id, "text_id": "c1", "title": "T", "content": content}

    def test_numbers_sources_and_skips_empty(self):
        with _config():
            sources = service._build_sources([
                self._row("s1", "<b>explained</b>"),
                self._row("s2", None),
                self._row("s3", "more"),
            ])

        assert [(s.ref, s.segment_id, s.content) for s in sources] == [(1, "s1", "explained"), (2, "s3", "more")]

    def test_truncates_each_source_and_stops_at_total_budget(self):
        with _config(SEGMENT_CHAT_MAX_SOURCE_CHARS=5, SEGMENT_CHAT_MAX_CONTEXT_CHARS=8):
            sources = service._build_sources([
                self._row("s1", "abcdefghij"),
                self._row("s2", "klmnop"),
                self._row("s3", "q"),
            ])

        assert [(s.content, s.truncated) for s in sources] == [("abcde", True), ("klm", True)]


class TestBuildSegmentContext:
    def _library(self, **overrides):
        options = dict(
            segment={"id": SEGMENT_ID, "text_id": "selText"},
            related_pages=[{
                "items": [
                    _item("c-seg", "comm"),
                    _item("t-seg", "trans"),
                    _item("r-seg", ROOT_ID),
                    _item("other-seg", "unrelated"),
                    _item("same-text-seg", "selText"),
                    _item("missing-text-seg", "missing"),
                ],
                "has_more": False,
            }],
            texts={
                "selText": {"title": {"en": "Selected Commentary"}, "language": "en", "commentary_of": ROOT_ID},
                "comm": {"title": {"bo": "Commentary"}, "language": "bo", "commentary_of": ROOT_ID, "license": "public"},
                "trans": {"title": {"en": "Translation"}, "language": "en", "translation_of": ROOT_ID},
                ROOT_ID: {"title": {"bo": "Root"}, "language": "bo"},
                "unrelated": {"title": {"en": "Unrelated"}},
                "selText-dup": {},
            },
            contents={
                SEGMENT_ID: "<p>selected verse</p>",
                "c-seg": "commentary text",
                "t-seg": "translation text",
                "r-seg": "root verse",
                "same-text-seg": "should be skipped",
            },
            links={"trans": "https://source.example"},
        )
        options.update(overrides)
        return FakeLibrary(**options)

    @pytest.mark.asyncio
    async def test_classifies_orders_and_reads_content_from_the_library(self):
        with _config(), patch.object(service, "LibraryClient", return_value=self._library()):
            context = await service.build_segment_context(SEGMENT_ID)

        assert context.segment.content == "selected verse"
        assert context.segment.text.model_dump() == {
            "text_id": "selText", "title": "Selected Commentary", "language": "en",
        }
        assert [(s.ref, s.type, s.segment_id, s.text_id) for s in context.sources] == [
            (1, SourceType.ROOT_TEXT, "r-seg", ROOT_ID),
            (2, SourceType.TRANSLATION, "t-seg", "trans"),
            (3, SourceType.COMMENTARY, "c-seg", "comm"),
        ]
        translation = context.sources[1]
        assert (translation.title, translation.language, translation.source_link) == (
            "Translation", "en", "https://source.example",
        )
        assert context.sources[2].license == "public"

    @pytest.mark.asyncio
    async def test_caps_number_of_sources(self):
        with _config(SEGMENT_CHAT_MAX_SOURCES=1), patch.object(
            service, "LibraryClient", return_value=self._library()
        ):
            context = await service.build_segment_context(SEGMENT_ID)

        assert [s.type for s in context.sources] == [SourceType.ROOT_TEXT]

    @pytest.mark.asyncio
    async def test_segment_without_text(self):
        library = self._library(segment={"id": SEGMENT_ID}, related_pages=[{"items": [], "has_more": False}])
        with _config(), patch.object(service, "LibraryClient", return_value=library):
            context = await service.build_segment_context(SEGMENT_ID)

        assert context.segment.text is None
        assert context.sources == []

    @pytest.mark.asyncio
    async def test_propagates_not_found(self):
        library = self._library(segment=SegmentNotFoundError(SEGMENT_ID))
        with _config(), patch.object(service, "LibraryClient", return_value=library):
            with pytest.raises(SegmentNotFoundError):
                await service.build_segment_context(SEGMENT_ID)

    @pytest.mark.asyncio
    async def test_propagates_library_errors(self):
        library = self._library(segment=httpx.ConnectError("down"))
        with _config(), patch.object(service, "LibraryClient", return_value=library):
            with pytest.raises(httpx.ConnectError):
                await service.build_segment_context(SEGMENT_ID)


def _context() -> SegmentChatContext:
    return SegmentChatContext.model_validate({
        "segment": {"segment_id": SEGMENT_ID, "content": "verse", "text": None},
        "sources": [],
    })


class TestGetSegmentContext:
    @pytest.mark.asyncio
    async def test_returns_cached_context(self):
        redis_client = AsyncMock()
        redis_client.get.return_value = _context().model_dump_json()
        build = AsyncMock()
        with _config(), patch.object(service, "get_redis_client", return_value=redis_client), patch.object(
            service, "build_segment_context", build
        ):
            context = await service.get_segment_context(SEGMENT_ID)

        assert context.segment.content == "verse"
        redis_client.get.assert_awaited_once_with(f"test:ctx:{SEGMENT_ID}")
        build.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_builds_and_caches_on_miss(self):
        redis_client = AsyncMock()
        redis_client.get.return_value = None
        with _config(), patch.object(service, "get_redis_client", return_value=redis_client), patch.object(
            service, "build_segment_context", AsyncMock(return_value=_context())
        ):
            await service.get_segment_context(SEGMENT_ID)

        key, ttl, _payload = redis_client.setex.await_args.args
        assert (key, ttl) == (f"test:ctx:{SEGMENT_ID}", 600)

    @pytest.mark.asyncio
    async def test_works_without_redis(self):
        redis_client = AsyncMock()
        redis_client.get.side_effect = ConnectionError("no redis")
        redis_client.setex.side_effect = ConnectionError("no redis")
        with _config(), patch.object(service, "get_redis_client", return_value=redis_client), patch.object(
            service, "build_segment_context", AsyncMock(return_value=_context())
        ):
            context = await service.get_segment_context(SEGMENT_ID)
        assert context.segment.segment_id == SEGMENT_ID

    @pytest.mark.asyncio
    async def test_ignores_corrupt_cache_entry(self):
        redis_client = AsyncMock()
        redis_client.get.return_value = b"not json"
        build = AsyncMock(return_value=_context())
        with _config(), patch.object(service, "get_redis_client", return_value=redis_client), patch.object(
            service, "build_segment_context", build
        ):
            await service.get_segment_context(SEGMENT_ID)
        build.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cache_disabled_skips_redis(self):
        get_client = MagicMock()
        with _config(SEGMENT_CHAT_CONTEXT_CACHE_TTL_SECONDS=0), patch.object(
            service, "get_redis_client", get_client
        ), patch.object(service, "build_segment_context", AsyncMock(return_value=_context())):
            await service.get_segment_context(SEGMENT_ID)
        get_client.assert_not_called()
