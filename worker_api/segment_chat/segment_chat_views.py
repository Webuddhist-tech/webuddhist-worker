import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette import status

from worker_api.config import get_bool
from worker_api.segment_chat.schemas import SegmentChatRequest
from worker_api.segment_chat.services import llm_client
from worker_api.segment_chat.services.library_client import SegmentNotFoundError
from worker_api.segment_chat.services.segment_chat_service import (
    is_rate_limited,
    stream_segment_chat,
)
from worker_api.segment_chat.services.segment_context_service import get_segment_context

logger = logging.getLogger(__name__)

segment_chat_router = APIRouter(prefix="/segment-chat", tags=["Segment Chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # Tells nginx not to buffer, so each event reaches the browser as it is sent.
    "X-Accel-Buffering": "no",
}


def _client_key(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@segment_chat_router.post(
    "/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_segment_chat_answer(body: SegmentChatRequest, request: Request):
    if not get_bool("SEGMENT_CHAT_ENABLED"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Segment chat is disabled")
    try:
        llm_client.get_llm_settings()
    except llm_client.LLMNotConfiguredError as exc:
        logger.error("Segment chat is not configured: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Segment chat is not configured"
        ) from exc

    if await is_rate_limited(_client_key(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many questions. Please wait a minute and try again.",
        )

    # Sources are gathered before the stream starts so a missing segment or a
    # library outage comes back as a normal HTTP error, not a broken stream.
    try:
        context = await get_segment_context(body.segment_id)
    except SegmentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Segment not found") from exc
    except httpx.HTTPError as exc:
        logger.warning("Segment chat could not read %s from the library: %s", body.segment_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not load the segment's sources"
        ) from exc

    return StreamingResponse(
        stream_segment_chat(body, context),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
