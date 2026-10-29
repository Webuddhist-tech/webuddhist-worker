"""Send the push for one verse-of-the-day comment window.

The backend debounces comments on today's verse into windows and, when one
closes, puts a VERSE_OF_DAY_COMMENTS event on the chat queue. The targets it
returns are the people who liked the verse, each with their own copy (it
counts only the comments that are news to them), so every device gets its
recipient's title and body.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional
from uuid import UUID

import httpx
import redis
from fastapi import HTTPException

from worker_api.config import get, get_int
from worker_api.notifications.schemas import (
    VerseOfDayCommentNotificationTargetsResponse,
    VerseOfDayCommentPushDevice,
    VerseOfDayCommentRecipient,
)
from worker_api.notifications.services.backend_client import (
    deactivate_push_device,
    fetch_verse_of_day_comment_notification_targets,
)
from worker_api.notifications.services.push.config_loader import is_push_configured
from worker_api.notifications.services.push.fcm_client import (
    PermanentPushTokenError,
    send_verse_of_day_comment_push_notification,
)

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


class TransientVerseCommentNotificationError(Exception):
    """Raised when processing should leave the SQS message for retry."""


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(get("CACHE_CONNECTION_STRING"))
    return _redis_client


def _idempotency_key(*, window_id: UUID, push_device_id: UUID) -> str:
    prefix = get("VERSE_OF_DAY_COMMENT_NOTIFICATION_IDEMPOTENCY_KEY_PREFIX")
    return f"{prefix}{window_id}:{push_device_id}"


def _already_sent(*, window_id: UUID, push_device_id: UUID) -> bool:
    client = _get_redis_client()
    return bool(client.exists(_idempotency_key(window_id=window_id, push_device_id=push_device_id)))


def _mark_sent(*, window_id: UUID, push_device_id: UUID) -> None:
    client = _get_redis_client()
    client.setex(
        _idempotency_key(window_id=window_id, push_device_id=push_device_id),
        get_int("VERSE_OF_DAY_COMMENT_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS"),
        "1",
    )


def _parse_uuid(value: Any) -> Optional[UUID]:
    if value is None or value == "":
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


async def _fetch_all_targets(window_id: UUID) -> VerseOfDayCommentNotificationTargetsResponse:
    page_size = max(get_int("VERSE_OF_DAY_COMMENT_NOTIFICATION_TARGET_PAGE_SIZE"), 1)
    skip = 0
    first_page: VerseOfDayCommentNotificationTargetsResponse | None = None
    all_recipients = []
    while True:
        try:
            page = await fetch_verse_of_day_comment_notification_targets(
                window_id=window_id, skip=skip, limit=page_size
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Comment window not found") from exc
            raise TransientVerseCommentNotificationError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise TransientVerseCommentNotificationError(str(exc)) from exc

        if first_page is None:
            first_page = page
        all_recipients.extend(page.recipients)
        if not page.has_more:
            break
        skip += page.limit

    assert first_page is not None
    return first_page.model_copy(update={"recipients": all_recipients, "has_more": False})


async def _send_to_device(
    *,
    targets: VerseOfDayCommentNotificationTargetsResponse,
    recipient: VerseOfDayCommentRecipient,
    device: VerseOfDayCommentPushDevice,
    semaphore: asyncio.Semaphore,
) -> str:
    """Return sent | skipped | permanent_failed | transient_failed."""
    async with semaphore:
        if not is_push_configured(device.platform):
            return "skipped"

        if _already_sent(window_id=targets.window_id, push_device_id=device.id):
            return "skipped"

        try:
            await send_verse_of_day_comment_push_notification(
                device_token=device.token,
                verse_id=targets.verse_id,
                title=recipient.title,
                body=recipient.body,
                image_url=targets.image_url,
            )
            _mark_sent(window_id=targets.window_id, push_device_id=device.id)
            return "sent"
        except PermanentPushTokenError:
            logger.warning(
                "Deactivating permanently invalid push device %s for verse comment window %s",
                device.id,
                targets.window_id,
            )
            try:
                await deactivate_push_device(push_device_id=device.id)
            except Exception:
                logger.exception("Failed to deactivate push device %s", device.id)
            _mark_sent(window_id=targets.window_id, push_device_id=device.id)
            return "permanent_failed"
        except Exception:
            logger.exception(
                "Transient FCM failure for device %s on verse comment window %s",
                device.id,
                targets.window_id,
            )
            return "transient_failed"


async def process_verse_of_day_comments_event(
    *,
    body: Dict[str, Any],
    receipt_handle: Optional[str],
    delete_message: Callable[[str], None],
) -> None:
    """Send one VERSE_OF_DAY_COMMENTS event; `delete_message` removes it from
    the queue it came from."""
    window_id = _parse_uuid(body.get("window_id"))
    if not window_id:
        logger.error("Invalid window_id in verse comment notification SQS message: %s", body)
        if receipt_handle:
            delete_message(receipt_handle)
        return

    try:
        targets = await _fetch_all_targets(window_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.error("Verse comment window not found for notification event %s", window_id)
            if receipt_handle:
                delete_message(receipt_handle)
            return
        raise TransientVerseCommentNotificationError(str(exc.detail)) from exc

    sends = [
        (recipient, device)
        for recipient in targets.recipients
        for device in recipient.push_devices
    ]
    if not sends:
        logger.info("No one to notify for verse comment window %s; deleting event", window_id)
        if receipt_handle:
            delete_message(receipt_handle)
        return

    semaphore = asyncio.Semaphore(max(get_int("VERSE_OF_DAY_COMMENT_NOTIFICATION_SEND_CONCURRENCY"), 1))
    results = await asyncio.gather(
        *[
            _send_to_device(targets=targets, recipient=recipient, device=device, semaphore=semaphore)
            for recipient, device in sends
        ]
    )

    logger.info(
        "Verse comment window %s processed: sent=%s permanent_failed=%s transient_failed=%s skipped=%s",
        window_id,
        results.count("sent"),
        results.count("permanent_failed"),
        results.count("transient_failed"),
        results.count("skipped"),
    )

    if results.count("transient_failed") > 0:
        raise TransientVerseCommentNotificationError(
            f"Transient failures remain for verse comment window {window_id}"
        )

    if receipt_handle:
        delete_message(receipt_handle)
