import asyncio
import html
import logging
import re
from typing import Any, Dict, List, Optional

import redis.asyncio as redis_async

from worker_api.config import get, get_int
from worker_api.segment_chat.schemas import (
    ChatSegment,
    ChatSource,
    SegmentChatContext,
    SegmentText,
    SourceType,
)
from worker_api.segment_chat.services.library_client import LibraryClient

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BLANK_LINES_RE = re.compile(r"\n{3,}")

# The root text anchors what is being commented on, translations are short and
# carry the meaning, commentaries carry the explanation. Sources are numbered
# and given to the model in this order until the context budget runs out.
_TYPE_ORDER = {
    SourceType.ROOT_TEXT: 0,
    SourceType.TRANSLATION: 1,
    SourceType.COMMENTARY: 2,
}

_redis_client: redis_async.Redis | None = None


def clean_content(value: Any) -> str:
    """Plain text from a segment's content. v2 content is already plain, but
    legacy data was HTML, so tags are stripped defensively."""
    if not value:
        return ""
    text = _BR_RE.sub("\n", str(value))
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def extract_title(title: Any) -> str:
    """Library titles are {language: title}; take the first non-empty one."""
    if isinstance(title, dict):
        for value in title.values():
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    if isinstance(title, str):
        return title.strip()
    return ""


def classify(
    related_text: Optional[Dict[str, Any]],
    related_text_id: str,
    selected_text: Optional[Dict[str, Any]],
) -> Optional[SourceType]:
    """What a related text is, relative to the selected segment's text.

    Matches the backend's resources panel: any text that is a translation of
    something counts as a translation, any commentary as a commentary. The
    text the selected one translates or comments on is its root text.
    """
    if related_text is None:
        return None
    if related_text.get("translation_of"):
        return SourceType.TRANSLATION
    if related_text.get("commentary_of"):
        return SourceType.COMMENTARY
    if selected_text and related_text_id in (
        selected_text.get("translation_of"),
        selected_text.get("commentary_of"),
    ):
        return SourceType.ROOT_TEXT
    return None


async def _fetch_related_items(client: LibraryClient, segment_id: str) -> List[Dict[str, Any]]:
    """Every related segment, deduplicated by id, across pages."""
    page_size = min(max(get_int("SEGMENT_CHAT_RELATED_PAGE_SIZE"), 1), 100)
    max_pages = max(get_int("SEGMENT_CHAT_MAX_RELATED_PAGES"), 1)
    items: Dict[str, Dict[str, Any]] = {}
    offset = 0

    for _ in range(max_pages):
        page = await client.get_related_page(segment_id, offset=offset, limit=page_size)
        for item in page.get("items") or []:
            if item.get("id") and item.get("text_id"):
                items.setdefault(item["id"], item)
        if not page.get("has_more"):
            break
        offset += page_size
    else:
        logger.warning(
            "Segment %s has more related segments than SEGMENT_CHAT_MAX_RELATED_PAGES allows; truncating",
            segment_id,
        )

    return list(items.values())


def _unique(values: List[Optional[str]]) -> List[str]:
    return list(dict.fromkeys(value for value in values if value))


def _build_sources(rows: List[Dict[str, Any]]) -> List[ChatSource]:
    """Numbered sources within the context budget. Rows are already in order."""
    max_total = get_int("SEGMENT_CHAT_MAX_CONTEXT_CHARS")
    max_per_source = get_int("SEGMENT_CHAT_MAX_SOURCE_CHARS")
    sources: List[ChatSource] = []
    used = 0

    for row in rows:
        content = clean_content(row.get("content"))
        if not content:
            continue
        remaining = max_total - used
        if remaining <= 0:
            break
        limit = min(max_per_source, remaining)
        truncated = len(content) > limit
        if truncated:
            content = content[:limit].rstrip()
        used += len(content)
        sources.append(
            ChatSource(
                ref=len(sources) + 1,
                type=row["type"],
                segment_id=row["segment_id"],
                text_id=row["text_id"],
                title=row.get("title") or "",
                language=row.get("language"),
                source_link=row.get("source_link"),
                license=row.get("license"),
                content=content,
                truncated=truncated,
            )
        )
    return sources


async def build_segment_context(segment_id: str) -> SegmentChatContext:
    """The selected segment and the content of every related segment, read
    straight from the OpenPecha library."""
    async with LibraryClient() as client:
        details, content, related_items = await asyncio.gather(
            client.get_segment(segment_id),
            client.get_segment_content(segment_id),
            _fetch_related_items(client, segment_id),
        )
        selected_text_id = details.get("text_id")
        related_items = [
            item
            for item in related_items
            if item["id"] != segment_id and item["text_id"] != selected_text_id
        ]

        text_ids = _unique([selected_text_id] + [item["text_id"] for item in related_items])
        text_payloads = await asyncio.gather(*[client.get_text(text_id) for text_id in text_ids])
        texts = dict(zip(text_ids, text_payloads))
        selected_text = texts.get(selected_text_id) if selected_text_id else None

        classified = []
        for item in related_items:
            source_type = classify(texts.get(item["text_id"]), item["text_id"], selected_text)
            if source_type is not None:
                classified.append((item, source_type))
        # Stable sort: within a type, the library's order is kept.
        classified.sort(key=lambda pair: _TYPE_ORDER[pair[1]])
        classified = classified[: max(get_int("SEGMENT_CHAT_MAX_SOURCES"), 0)]

        source_text_ids = _unique([item["text_id"] for item, _ in classified])
        contents, source_links = await asyncio.gather(
            asyncio.gather(*[client.get_segment_content(item["id"]) for item, _ in classified]),
            asyncio.gather(*[client.get_text_source_link(text_id) for text_id in source_text_ids]),
        )
    links = dict(zip(source_text_ids, source_links))

    rows = []
    for (item, source_type), item_content in zip(classified, contents):
        text = texts.get(item["text_id"]) or {}
        rows.append(
            {
                "type": source_type,
                "segment_id": item["id"],
                "text_id": item["text_id"],
                "title": extract_title(text.get("title")),
                "language": text.get("language"),
                "source_link": links.get(item["text_id"]),
                "license": text.get("license"),
                "content": item_content,
            }
        )

    segment = ChatSegment(
        segment_id=segment_id,
        content=clean_content(content),
        text=SegmentText(
            text_id=selected_text_id,
            title=extract_title((selected_text or {}).get("title")) or None,
            language=(selected_text or {}).get("language"),
        )
        if selected_text_id
        else None,
    )
    return SegmentChatContext(segment=segment, sources=_build_sources(rows))


def get_redis_client() -> redis_async.Redis:
    global _redis_client
    if _redis_client is None:
        # Short timeouts: the cache is optional and must never stall a chat.
        _redis_client = redis_async.Redis.from_url(
            get("CACHE_CONNECTION_STRING"),
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis_client


def _cache_key(segment_id: str) -> str:
    return f"{get('SEGMENT_CHAT_CONTEXT_CACHE_KEY_PREFIX')}{segment_id}"


async def _read_cache(segment_id: str) -> Optional[SegmentChatContext]:
    try:
        raw = await get_redis_client().get(_cache_key(segment_id))
    except Exception:
        logger.warning("Segment chat context cache read failed", exc_info=True)
        return None
    if not raw:
        return None
    try:
        return SegmentChatContext.model_validate_json(raw)
    except ValueError:
        return None


async def _write_cache(segment_id: str, context: SegmentChatContext) -> None:
    ttl = get_int("SEGMENT_CHAT_CONTEXT_CACHE_TTL_SECONDS")
    if ttl <= 0:
        return
    try:
        await get_redis_client().setex(_cache_key(segment_id), ttl, context.model_dump_json())
    except Exception:
        logger.warning("Segment chat context cache write failed", exc_info=True)


async def get_segment_context(segment_id: str) -> SegmentChatContext:
    """Segment plus its related translations, commentaries and root text,
    cached in Redis so a follow-up question on the same segment does not
    refetch everything. The cache is best effort: without Redis every request
    fetches."""
    use_cache = get_int("SEGMENT_CHAT_CONTEXT_CACHE_TTL_SECONDS") > 0
    if use_cache:
        cached = await _read_cache(segment_id)
        if cached is not None:
            return cached

    context = await build_segment_context(segment_id)
    if use_cache:
        await _write_cache(segment_id, context)
    return context
