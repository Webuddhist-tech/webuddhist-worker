import asyncio
import logging
from typing import Any, Callable, Dict, Optional
from uuid import UUID

import httpx
import redis
from fastapi import HTTPException

from worker_api.config import get, get_int
from worker_api.notifications.schemas import (
    ChatPushDeviceTarget,
    PrayerNotificationTargetsResponse,
)
from worker_api.notifications.services.backend_client import (
    deactivate_push_device,
    fetch_prayer_notification_targets,
)
from worker_api.notifications.services.push.config_loader import is_push_configured
from worker_api.notifications.services.push.fcm_client import (
    PermanentPushTokenError,
    send_prayer_push_notification,
)

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


class TransientPrayerNotificationError(Exception):
    """Raised when processing should leave the SQS message for retry."""


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(get("CACHE_CONNECTION_STRING"))
    return _redis_client


def _idempotency_key(*, prayer_id: UUID, push_device_id: UUID) -> str:
    """Keyed on the prayer, not the request message, so two people praying for
    the same request are two notifications rather than one deduped away."""
    prefix = get("PRAYER_NOTIFICATION_IDEMPOTENCY_KEY_PREFIX")
    return f"{prefix}{prayer_id}:{push_device_id}"


def _already_sent(*, prayer_id: UUID, push_device_id: UUID) -> bool:
    client = _get_redis_client()
    return bool(client.exists(_idempotency_key(prayer_id=prayer_id, push_device_id=push_device_id)))


def _mark_sent(*, prayer_id: UUID, push_device_id: UUID) -> None:
    client = _get_redis_client()
    client.setex(
        _idempotency_key(prayer_id=prayer_id, push_device_id=push_device_id),
        get_int("PRAYER_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS"),
        "1",
    )


def _parse_uuid(value: Any) -> Optional[UUID]:
    if value is None or value == "":
        return None
    return UUID(str(value))


async def _fetch_all_targets(prayer_id: UUID) -> PrayerNotificationTargetsResponse:
    page_size = max(get_int("PRAYER_NOTIFICATION_TARGET_PAGE_SIZE"), 1)
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
            raise TransientPrayerNotificationError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise TransientPrayerNotificationError(str(exc)) from exc

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
    targets: PrayerNotificationTargetsResponse,
    device: ChatPushDeviceTarget,
    semaphore: asyncio.Semaphore,
) -> str:
    """Return sent | skipped | permanent_failed | transient_failed."""
    async with semaphore:
        if not is_push_configured(device.platform):
            return "skipped"

        if _already_sent(prayer_id=targets.prayer_id, push_device_id=device.id):
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
                image_url=targets.image_url,
            )
            _mark_sent(prayer_id=targets.prayer_id, push_device_id=device.id)
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
            _mark_sent(prayer_id=targets.prayer_id, push_device_id=device.id)
            return "permanent_failed"
        except Exception:
            logger.exception(
                "Transient FCM failure for device %s on prayer %s",
                device.id,
                targets.prayer_id,
            )
            return "transient_failed"


async def process_prayer_event(
    *,
    body: Dict[str, Any],
    receipt_handle: Optional[str],
    delete_message: Callable[[str], None],
) -> None:
    """Send one PRAYER_RECEIVED event. `delete_message` removes it from whichever
    queue it came from (the prayer queue, or the chat queue for legacy events)."""
    prayer_id = _parse_uuid(body.get("prayer_id"))
    if not prayer_id:
        logger.error("Invalid prayer_id in prayer notification SQS message: %s", body)
        if receipt_handle:
            delete_message(receipt_handle)
        return

    try:
        targets = await _fetch_all_targets(prayer_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.error("Prayer not found for notification event %s", prayer_id)
            if receipt_handle:
                delete_message(receipt_handle)
            return
        raise TransientPrayerNotificationError(str(exc.detail)) from exc

    devices = [
        device for recipient in targets.recipients for device in recipient.push_devices
    ]
    if not devices:
        logger.info("No push devices for prayer %s; deleting event", prayer_id)
        if receipt_handle:
            delete_message(receipt_handle)
        return

    concurrency = max(get_int("PRAYER_NOTIFICATION_SEND_CONCURRENCY"), 1)
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *[
            _send_to_device(targets=targets, device=device, semaphore=semaphore)
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
        raise TransientPrayerNotificationError(
            f"Transient failures remain for prayer {prayer_id}"
        )

    if receipt_handle:
        delete_message(receipt_handle)
