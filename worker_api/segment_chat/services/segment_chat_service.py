import json
import logging
import re
from typing import AsyncIterator, Optional

from worker_api.config import get, get_int
from worker_api.segment_chat.schemas import SegmentChatContext, SegmentChatRequest
from worker_api.segment_chat.services import llm_client
from worker_api.segment_chat.services.chat_prompt import build_system_prompt, build_user_message
from worker_api.segment_chat.services.segment_context_service import get_redis_client

logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[(\d+)\]")


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def cited_refs(answer: str, valid_refs: set[int]) -> list[int]:
    """Citation numbers used in the answer, in first-use order."""
    seen: list[int] = []
    for match in _CITATION_RE.finditer(answer):
        ref = int(match.group(1))
        if ref in valid_refs and ref not in seen:
            seen.append(ref)
    return seen


async def is_rate_limited(client_key: str) -> bool:
    """Fixed one-minute window per client. Fails open when Redis is down."""
    limit = get_int("SEGMENT_CHAT_RATE_LIMIT_PER_MINUTE")
    if limit <= 0:
        return False
    key = f"{get('SEGMENT_CHAT_RATE_LIMIT_KEY_PREFIX')}{client_key}"
    try:
        client = get_redis_client()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, 60)
    except Exception:
        logger.warning("Segment chat rate limit check failed; allowing request", exc_info=True)
        return False
    return count > limit


async def stream_segment_chat(
    request: SegmentChatRequest,
    context: SegmentChatContext,
) -> AsyncIterator[str]:
    """SSE stream: one `sources` event, `delta` events with answer text, then
    `done` (or `error` if the model fails part way)."""
    yield sse_event(
        "sources",
        {
            "segment": context.segment.model_dump(mode="json"),
            "sources": [source.to_public() for source in context.sources],
        },
    )

    answer_parts: list[str] = []
    error: Optional[str] = None
    try:
        async for text in llm_client.stream_completion(
            system_prompt=build_system_prompt(request.language),
            history=request.history,
            user_message=build_user_message(context, request.question),
        ):
            answer_parts.append(text)
            yield sse_event("delta", {"text": text})
    except Exception as exc:
        logger.exception("Segment chat LLM stream failed for segment %s", request.segment_id)
        error = type(exc).__name__

    if error:
        yield sse_event("error", {"message": "The answer could not be completed. Please try again."})
        return

    refs = cited_refs("".join(answer_parts), {source.ref for source in context.sources})
    yield sse_event("done", {"cited_refs": refs})
