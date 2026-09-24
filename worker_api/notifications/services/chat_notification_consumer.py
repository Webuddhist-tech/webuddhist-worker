import asyncio
import logging
from typing import Any, Dict, Optional
from uuid import UUID

import httpx
import redis
from fastapi import HTTPException

from worker_api.config import get, get_bool, get_int
from worker_api.notifications.chat_sqs_client import (
    PRAYER_RECEIVED_EVENT,
    delete_chat_notification_message,
    is_chat_notification_sqs_poll_enabled,
    parse_chat_notification_message_body,
    receive_chat_notification_messages,
)
from worker_api.notifications.schemas import (
    ChatNotificationTargetsResponse,
    ChatPushDeviceTarget,
    PrayerNotificationTargetsResponse,
)
from worker_api.notifications.services.backend_client import (
    deactivate_push_device,
    fetch_chat_notification_targets,
    fetch_prayer_notification_targets,
)
from worker_api.notifications.services.push.config_loader import is_push_configured
from worker_api.notifications.services.push.fcm_client import (
    PermanentPushTokenError,
    send_chat_push_notification,
    send_prayer_push_notification,
)

logger = logging.getLogger(__name__)

_POLL_IDLE_SECONDS = 5
_POLL_ERROR_SECONDS = 10
_redis_client: redis.Redis | None = None


class TransientChatNotificationError(Exception):
    """Raised when processing should leave the SQS message for retry."""


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(get("CACHE_CONNECTION_STRING"))
    return _redis_client


def _idempotency_key(*, message_id: UUID, push_device_id: UUID) -> str:
    """Keyed on the event, not the message: a chat message passes its own id, a
    prayer passes its prayer_id, so two people praying for the same request are
    two notifications rather than one deduped away."""
    prefix = get("CHAT_NOTIFICATION_IDEMPOTENCY_KEY_PREFIX")
    return f"{prefix}{message_id}:{push_device_id}"


def _already_sent(*, message_id: UUID, push_device_id: UUID) -> bool:
    client = _get_redis_client()
    return bool(client.exists(_idempotency_key(message_id=message_id, push_device_id=push_device_id)))


def _mark_sent(*, message_id: UUID, push_device_id: UUID) -> None:
    client = _get_redis_client()
    client.setex(
        _idempotency_key(message_id=message_id, push_device_id=push_device_id),
        get_int("CHAT_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS"),
        "1",
    )


def _parse_uuid(value: Any) -> Optional[UUID]:
    if value is None or value == "":
        return None
    return UUID(str(value))


async def _fetch_all_targets(message_id: UUID) -> ChatNotificationTargetsResponse:
    page_size = max(get_int("CHAT_NOTIFICATION_TARGET_PAGE_SIZE"), 1)
    skip = 0
    first_page: ChatNotificationTargetsResponse | None = None
    all_recipients = []

    while True:
        try:
            page = await fetch_chat_notification_targets(
                message_id=message_id,
                skip=skip,
                limit=page_size,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Chat message not found") from exc
            raise TransientChatNotificationError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise TransientChatNotificationError(str(exc)) from exc

        if first_page is None:
            first_page = page
        all_recipients.extend(page.recipients)
        if not page.has_more:
            break
        skip += page.limit

    assert first_page is not None
    return first_page.model_copy(update={"recipients": all_recipients, "has_more": False})


async def _fetch_all_prayer_targets(prayer_id: UUID) -> PrayerNotificationTargetsResponse:
    page_size = max(get_int("CHAT_NOTIFICATION_TARGET_PAGE_SIZE"), 1)
    skip = 0
    first_page: PrayerNotificationTargetsResponse | None = None
    all_recipients = []

    while True:
        try:
            page = await fetch_prayer_notification_targets(
                prayer_id=prayer_id,
                skip=skip,
                limit=page_size,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Prayer not found") from exc
            raise TransientChatNotificationError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise TransientChatNotificationError(str(exc)) from exc

        if first_page is None:
            first_page = page
        all_recipients.extend(page.recipients)
        if not page.has_more:
            break
        skip += page.limit

    assert first_page is not None
    return first_page.model_copy(update={"recipients": all_recipients, "has_more": False})


async def _send_prayer_to_device(
    *,
    targets: PrayerNotificationTargetsResponse,
    device: ChatPushDeviceTarget,
    semaphore: asyncio.Semaphore,
) -> str:
    """Return sent | skipped | permanent_failed | transient_failed."""
    async with semaphore:
        if not is_push_configured(device.platform):
            return "skipped"

        if _already_sent(message_id=targets.prayer_id, push_device_id=device.id):
            return "skipped"

        try:
            await send_prayer_push_notification(
                device_token=device.token,
                room_id=targets.room_id,
                message_id=targets.message_id,
                prayer_id=targets.prayer_id,
                chat_kind=targets.chat_kind,
                group_id=targets.group_id,
                event_id=targets.event_id,
                prayer_count=targets.prayer_count,
                title=targets.title,
                body=targets.body,
            )
            _mark_sent(message_id=targets.prayer_id, push_device_id=device.id)
            return "sent"
        except PermanentPushTokenError:
            logger.warning(
                "Deactivating permanently invalid push device %s for prayer %s",
                device.id,
                targets.prayer_id,
            )
            try:
                await deactivate_push_device(push_device_id=device.id)
            except Exception:
                logger.exception("Failed to deactivate push device %s", device.id)
            _mark_sent(message_id=targets.prayer_id, push_device_id=device.id)
            return "permanent_failed"
        except Exception:
            logger.exception(
                "Transient FCM failure for device %s on prayer %s",
                device.id,
                targets.prayer_id,
            )
            return "transient_failed"


async def _process_prayer_notification(
    *, body: Dict[str, Any], receipt_handle: Optional[str]
) -> None:
    prayer_id = _parse_uuid(body.get("prayer_id"))
    if not prayer_id:
        logger.error("Invalid prayer_id in prayer notification SQS message: %s", body)
        if receipt_handle:
            delete_chat_notification_message(receipt_handle)
        return

    try:
        targets = await _fetch_all_prayer_targets(prayer_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.error("Prayer not found for notification event %s", prayer_id)
            if receipt_handle:
                delete_chat_notification_message(receipt_handle)
            return
        raise TransientChatNotificationError(str(exc.detail)) from exc

    devices = [
        device for recipient in targets.recipients for device in recipient.push_devices
    ]
    if not devices:
        logger.info("No push devices for prayer %s; deleting event", prayer_id)
        if receipt_handle:
            delete_chat_notification_message(receipt_handle)
        return

    concurrency = max(get_int("CHAT_NOTIFICATION_SEND_CONCURRENCY"), 1)
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *[
            _send_prayer_to_device(targets=targets, device=device, semaphore=semaphore)
            for device in devices
        ]
    )

    logger.info(
        "Prayer notification %s processed: sent=%s permanent_failed=%s transient_failed=%s skipped=%s",
        prayer_id,
        results.count("sent"),
        results.count("permanent_failed"),
        results.count("transient_failed"),
        results.count("skipped"),
    )

    if results.count("transient_failed") > 0:
        raise TransientChatNotificationError(
            f"Transient failures remain for prayer {prayer_id}"
        )

    if receipt_handle:
        delete_chat_notification_message(receipt_handle)


async def _send_to_device(
    *,
    targets: ChatNotificationTargetsResponse,
    device: ChatPushDeviceTarget,
    semaphore: asyncio.Semaphore,
) -> str:
    """Return sent | skipped | permanent_failed | transient_failed."""
    async with semaphore:
        if not is_push_configured(device.platform):
            return "skipped"

        if _already_sent(message_id=targets.message_id, push_device_id=device.id):
            return "skipped"

        try:
            await send_chat_push_notification(
                device_token=device.token,
                room_id=targets.room_id,
                message_id=targets.message_id,
                sender_id=targets.sender_id,
                chat_kind=targets.chat_kind,
                group_id=targets.group_id,
                title=targets.title,
                body=targets.body,
                message_type=targets.message_type,
                image_url=targets.image_url,
            )
            _mark_sent(message_id=targets.message_id, push_device_id=device.id)
            return "sent"
        except PermanentPushTokenError:
            logger.warning(
                "Deactivating permanently invalid push device %s for chat message %s",
                device.id,
                targets.message_id,
            )
            try:
                await deactivate_push_device(push_device_id=device.id)
            except Exception:
                logger.exception("Failed to deactivate push device %s", device.id)
            _mark_sent(message_id=targets.message_id, push_device_id=device.id)
            return "permanent_failed"
        except Exception:
            logger.exception(
                "Transient FCM failure for device %s on chat message %s",
                device.id,
                targets.message_id,
            )
            return "transient_failed"


async def process_chat_notification_message(message: Dict[str, Any]) -> None:
    receipt_handle = message.get("ReceiptHandle")
    body = parse_chat_notification_message_body(message.get("Body", ""))
    if not body:
        if receipt_handle:
            delete_chat_notification_message(receipt_handle)
        return

    if not get_bool("NOTIFICATION_DISPATCH_ENABLED"):
        logger.info("Chat notification dispatch disabled; deleting event %s", body)
        if receipt_handle:
            delete_chat_notification_message(receipt_handle)
        return

    # Prayer notifications share this queue and consumer, keyed on prayer_id.
    if body.get("event_type") == PRAYER_RECEIVED_EVENT:
        await _process_prayer_notification(body=body, receipt_handle=receipt_handle)
        return

    message_id = _parse_uuid(body.get("message_id"))
    if not message_id:
        logger.error("Invalid message_id in chat notification SQS message: %s", body)
        if receipt_handle:
            delete_chat_notification_message(receipt_handle)
        return

    try:
        targets = await _fetch_all_targets(message_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.error("Chat message not found for notification event %s", message_id)
            if receipt_handle:
                delete_chat_notification_message(receipt_handle)
            return
        raise TransientChatNotificationError(str(exc.detail)) from exc

    devices = [
        device
        for recipient in targets.recipients
        for device in recipient.push_devices
    ]
    if not devices:
        logger.info("No push devices for chat message %s; deleting event", message_id)
        if receipt_handle:
            delete_chat_notification_message(receipt_handle)
        return

    concurrency = max(get_int("CHAT_NOTIFICATION_SEND_CONCURRENCY"), 1)
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *[
            _send_to_device(targets=targets, device=device, semaphore=semaphore)
            for device in devices
        ]
    )

    sent = results.count("sent")
    skipped = results.count("skipped")
    permanent_failed = results.count("permanent_failed")
    transient_failed = results.count("transient_failed")
    logger.info(
        "Chat notification %s processed: sent=%s permanent_failed=%s transient_failed=%s skipped=%s",
        message_id,
        sent,
        permanent_failed,
        transient_failed,
        skipped,
    )

    if transient_failed > 0:
        raise TransientChatNotificationError(
            f"Transient failures remain for chat message {message_id}"
        )

    if receipt_handle:
        delete_chat_notification_message(receipt_handle)


async def run_chat_notification_sqs_consumer(stop_event: asyncio.Event) -> None:
    logger.info("Chat notification SQS consumer started")
    while not stop_event.is_set():
        if not is_chat_notification_sqs_poll_enabled():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_IDLE_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        try:
            messages = await asyncio.to_thread(receive_chat_notification_messages)
            if not messages:
                continue
            for message in messages:
                if stop_event.is_set():
                    break
                try:
                    await process_chat_notification_message(message)
                except TransientChatNotificationError:
                    logger.warning(
                        "Leaving chat notification SQS message for retry: %s",
                        message.get("MessageId"),
                    )
                except Exception:
                    logger.exception("Unexpected chat notification consumer error")
        except Exception:
            logger.exception("Chat notification SQS consumer loop error")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_ERROR_SECONDS)
            except asyncio.TimeoutError:
                pass

    logger.info("Chat notification SQS consumer stopped")
