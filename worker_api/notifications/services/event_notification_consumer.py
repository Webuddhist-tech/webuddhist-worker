import asyncio
import logging
from typing import Any, Dict, Optional
from uuid import UUID

import httpx
import redis
from fastapi import HTTPException

from worker_api.config import get, get_bool, get_int
from worker_api.notifications.event_sqs_client import (
    EVENT_ANNOUNCEMENT_EVENT,
    EVENT_CREATED_EVENT,
    EVENT_REMINDER_EVENT,
    delete_event_notification_message,
    is_event_notification_sqs_poll_enabled,
    parse_event_notification_message_body,
    receive_event_notification_messages,
)
from worker_api.notifications.schemas import (
    EventAnnouncementTargetsResponse,
    EventNotificationTargetsResponse,
    EventPushDeviceTarget,
    EventReminderTargetsResponse,
)
from worker_api.notifications.services.backend_client import (
    deactivate_push_device,
    fetch_event_announcement_targets,
    fetch_event_notification_targets,
    fetch_event_reminder_targets,
)
from worker_api.notifications.services.push.config_loader import is_push_configured
from worker_api.notifications.services.push.fcm_client import (
    PermanentPushTokenError,
    send_event_announcement_push_notification,
    send_event_push_notification,
    send_event_reminder_push_notification,
)

logger = logging.getLogger(__name__)

_POLL_IDLE_SECONDS = 5
_POLL_ERROR_SECONDS = 10
_redis_client: redis.Redis | None = None


class TransientEventNotificationError(Exception):
    """Raised when processing should leave the SQS message for retry."""


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(get("CACHE_CONNECTION_STRING"))
    return _redis_client


def _idempotency_key(
    *,
    event_id: UUID,
    push_device_id: UUID,
    reminder_type: Optional[str] = None,
    fire_at: Optional[str] = None,
    announcement_id: Optional[str] = None,
) -> str:
    """A key per delivery, not per event.

    fire_at is what separates one occurrence-day from the next. A multi-day
    or recurring event sends the same (event_id, reminder_type) pair on every
    day it runs, and consecutive days are exactly 24h apart - the same as
    EVENT_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS - so a key without it would
    race the TTL and silently drop day two onward.

    Messages predating fire_at keep the old key shape, so a deploy does not
    strand anything already queued."""
    prefix = get("EVENT_NOTIFICATION_IDEMPOTENCY_KEY_PREFIX")
    if announcement_id:
        return f"{prefix}{event_id}:ANNOUNCEMENT:{announcement_id}:{push_device_id}"
    if reminder_type:
        if fire_at:
            return f"{prefix}{event_id}:{reminder_type}:{fire_at}:{push_device_id}"
        return f"{prefix}{event_id}:{reminder_type}:{push_device_id}"
    return f"{prefix}{event_id}:{push_device_id}"


def _already_sent(
    *,
    event_id: UUID,
    push_device_id: UUID,
    reminder_type: Optional[str] = None,
    fire_at: Optional[str] = None,
    announcement_id: Optional[str] = None,
) -> bool:
    client = _get_redis_client()
    return bool(
        client.exists(
            _idempotency_key(
                event_id=event_id,
                push_device_id=push_device_id,
                reminder_type=reminder_type,
                fire_at=fire_at,
                announcement_id=announcement_id,
            )
        )
    )


def _mark_sent(
    *,
    event_id: UUID,
    push_device_id: UUID,
    reminder_type: Optional[str] = None,
    fire_at: Optional[str] = None,
    announcement_id: Optional[str] = None,
) -> None:
    client = _get_redis_client()
    client.setex(
        _idempotency_key(
            event_id=event_id,
            push_device_id=push_device_id,
            reminder_type=reminder_type,
            fire_at=fire_at,
            announcement_id=announcement_id,
        ),
        get_int("EVENT_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS"),
        "1",
    )


def _parse_uuid(value: Any) -> Optional[UUID]:
    if value is None or value == "":
        return None
    return UUID(str(value))


async def _fetch_all_targets(event_id: UUID) -> EventNotificationTargetsResponse:
    page_size = max(get_int("EVENT_NOTIFICATION_TARGET_PAGE_SIZE"), 1)
    skip = 0
    first_page: EventNotificationTargetsResponse | None = None
    all_recipients = []

    while True:
        try:
            page = await fetch_event_notification_targets(
                event_id=event_id,
                skip=skip,
                limit=page_size,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Event not found") from exc
            raise TransientEventNotificationError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise TransientEventNotificationError(str(exc)) from exc

        if first_page is None:
            first_page = page
        all_recipients.extend(page.recipients)
        if not page.has_more:
            break
        skip += page.limit

    assert first_page is not None
    return first_page.model_copy(update={"recipients": all_recipients, "has_more": False})


async def _fetch_all_reminder_targets(
    event_id: UUID, reminder_type: str, fire_at: str | None
) -> EventReminderTargetsResponse:
    page_size = max(get_int("EVENT_NOTIFICATION_TARGET_PAGE_SIZE"), 1)
    skip = 0
    first_page: EventReminderTargetsResponse | None = None
    all_recipients = []

    while True:
        try:
            page = await fetch_event_reminder_targets(
                event_id=event_id,
                reminder_type=reminder_type,
                fire_at=fire_at,
                skip=skip,
                limit=page_size,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Event not found") from exc
            raise TransientEventNotificationError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise TransientEventNotificationError(str(exc)) from exc

        if first_page is None:
            first_page = page
        all_recipients.extend(page.recipients)
        if not page.has_more:
            break
        skip += page.limit

    assert first_page is not None
    return first_page.model_copy(update={"recipients": all_recipients, "has_more": False})


async def _fetch_all_announcement_targets(
    event_id: UUID, audience: str
) -> EventAnnouncementTargetsResponse:
    page_size = max(get_int("EVENT_NOTIFICATION_TARGET_PAGE_SIZE"), 1)
    skip = 0
    first_page: EventAnnouncementTargetsResponse | None = None
    all_recipients = []

    while True:
        try:
            page = await fetch_event_announcement_targets(
                event_id=event_id,
                audience=audience,
                skip=skip,
                limit=page_size,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Event not found") from exc
            raise TransientEventNotificationError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise TransientEventNotificationError(str(exc)) from exc

        if first_page is None:
            first_page = page
        all_recipients.extend(page.recipients)
        if not page.has_more:
            break
        skip += page.limit

    assert first_page is not None
    return first_page.model_copy(update={"recipients": all_recipients, "has_more": False})


async def _send_announcement_to_device(
    *,
    event_id: UUID,
    announcement_id: str,
    title: str,
    body: str,
    device: EventPushDeviceTarget,
    semaphore: asyncio.Semaphore,
) -> str:
    """Return sent | skipped | permanent_failed | transient_failed."""
    async with semaphore:
        if not is_push_configured(device.platform):
            return "skipped"

        if _already_sent(
            event_id=event_id,
            push_device_id=device.id,
            announcement_id=announcement_id,
        ):
            return "skipped"

        try:
            await send_event_announcement_push_notification(
                device_token=device.token,
                event_id=event_id,
                announcement_id=announcement_id,
                title=title,
                body=body,
            )
            _mark_sent(
                event_id=event_id,
                push_device_id=device.id,
                announcement_id=announcement_id,
            )
            return "sent"
        except PermanentPushTokenError:
            logger.warning(
                "Deactivating permanently invalid push device %s for event announcement %s",
                device.id,
                event_id,
            )
            try:
                await deactivate_push_device(push_device_id=device.id)
            except Exception:
                logger.exception("Failed to deactivate push device %s", device.id)
            _mark_sent(
                event_id=event_id,
                push_device_id=device.id,
                announcement_id=announcement_id,
            )
            return "permanent_failed"
        except Exception:
            logger.exception(
                "Transient FCM failure for device %s on event announcement %s",
                device.id,
                event_id,
            )
            return "transient_failed"


async def _send_to_device(
    *,
    targets: EventNotificationTargetsResponse,
    device: EventPushDeviceTarget,
    semaphore: asyncio.Semaphore,
) -> str:
    """Return sent | skipped | permanent_failed | transient_failed."""
    async with semaphore:
        if not is_push_configured(device.platform):
            return "skipped"

        if _already_sent(event_id=targets.event_id, push_device_id=device.id):
            return "skipped"

        try:
            await send_event_push_notification(
                device_token=device.token,
                event_id=targets.event_id,
                group_id=targets.group_id,
                author_id=targets.author_id,
                title=targets.title,
                body=targets.body,
            )
            _mark_sent(event_id=targets.event_id, push_device_id=device.id)
            return "sent"
        except PermanentPushTokenError:
            logger.warning(
                "Deactivating permanently invalid push device %s for event %s",
                device.id,
                targets.event_id,
            )
            try:
                await deactivate_push_device(push_device_id=device.id)
            except Exception:
                logger.exception("Failed to deactivate push device %s", device.id)
            _mark_sent(event_id=targets.event_id, push_device_id=device.id)
            return "permanent_failed"
        except Exception:
            logger.exception(
                "Transient FCM failure for device %s on event %s",
                device.id,
                targets.event_id,
            )
            return "transient_failed"


async def _send_reminder_to_device(
    *,
    targets: EventReminderTargetsResponse,
    device: EventPushDeviceTarget,
    semaphore: asyncio.Semaphore,
    fire_at: Optional[str] = None,
) -> str:
    """Return sent | skipped | permanent_failed | transient_failed."""
    async with semaphore:
        if not is_push_configured(device.platform):
            return "skipped"

        if _already_sent(
            event_id=targets.event_id,
            push_device_id=device.id,
            reminder_type=targets.reminder_type,
            fire_at=fire_at,
        ):
            return "skipped"

        try:
            await send_event_reminder_push_notification(
                device_token=device.token,
                event_id=targets.event_id,
                reminder_type=targets.reminder_type,
                title=targets.title,
                body=targets.body,
            )
            _mark_sent(
                event_id=targets.event_id,
                push_device_id=device.id,
                reminder_type=targets.reminder_type,
                fire_at=fire_at,
            )
            return "sent"
        except PermanentPushTokenError:
            logger.warning(
                "Deactivating permanently invalid push device %s for event reminder %s",
                device.id,
                targets.event_id,
            )
            try:
                await deactivate_push_device(push_device_id=device.id)
            except Exception:
                logger.exception("Failed to deactivate push device %s", device.id)
            _mark_sent(
                event_id=targets.event_id,
                push_device_id=device.id,
                reminder_type=targets.reminder_type,
                fire_at=fire_at,
            )
            return "permanent_failed"
        except Exception:
            logger.exception(
                "Transient FCM failure for device %s on event reminder %s",
                device.id,
                targets.event_id,
            )
            return "transient_failed"


async def _process_event_created(event_id: UUID, receipt_handle: Optional[str]) -> None:
    try:
        targets = await _fetch_all_targets(event_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.error("Event not found for notification event %s", event_id)
            if receipt_handle:
                delete_event_notification_message(receipt_handle)
            return
        raise TransientEventNotificationError(str(exc.detail)) from exc

    devices = [
        device
        for recipient in targets.recipients
        for device in recipient.push_devices
    ]
    if not devices:
        logger.info("No push devices for event %s; deleting event", event_id)
        if receipt_handle:
            delete_event_notification_message(receipt_handle)
        return

    concurrency = max(get_int("EVENT_NOTIFICATION_SEND_CONCURRENCY"), 1)
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
        "Event notification %s processed: sent=%s permanent_failed=%s transient_failed=%s skipped=%s",
        event_id,
        sent,
        permanent_failed,
        transient_failed,
        skipped,
    )

    if transient_failed > 0:
        raise TransientEventNotificationError(
            f"Transient failures remain for event {event_id}"
        )

    if receipt_handle:
        delete_event_notification_message(receipt_handle)


async def _process_event_reminder(
    event_id: UUID, reminder_type: str, fire_at: Optional[str], receipt_handle: Optional[str]
) -> None:
    try:
        targets = await _fetch_all_reminder_targets(event_id, reminder_type, fire_at)
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.error(
                "Event not found for reminder %s on event %s", reminder_type, event_id
            )
            if receipt_handle:
                delete_event_notification_message(receipt_handle)
            return
        raise TransientEventNotificationError(str(exc.detail)) from exc

    devices = [
        device
        for recipient in targets.recipients
        for device in recipient.push_devices
    ]
    if not devices:
        logger.info(
            "No push devices for event reminder %s on event %s; deleting event",
            reminder_type,
            event_id,
        )
        if receipt_handle:
            delete_event_notification_message(receipt_handle)
        return

    concurrency = max(get_int("EVENT_NOTIFICATION_SEND_CONCURRENCY"), 1)
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *[
            _send_reminder_to_device(
                targets=targets, device=device, semaphore=semaphore, fire_at=fire_at
            )
            for device in devices
        ]
    )

    sent = results.count("sent")
    skipped = results.count("skipped")
    permanent_failed = results.count("permanent_failed")
    transient_failed = results.count("transient_failed")
    logger.info(
        "Event reminder %s for event %s processed: sent=%s permanent_failed=%s transient_failed=%s skipped=%s",
        reminder_type,
        event_id,
        sent,
        permanent_failed,
        transient_failed,
        skipped,
    )

    if transient_failed > 0:
        raise TransientEventNotificationError(
            f"Transient failures remain for event {event_id} reminder {reminder_type}"
        )

    if receipt_handle:
        delete_event_notification_message(receipt_handle)


async def process_event_notification_message(message: Dict[str, Any]) -> None:
    receipt_handle = message.get("ReceiptHandle")
    body = parse_event_notification_message_body(message.get("Body", ""))
    if not body:
        if receipt_handle:
            delete_event_notification_message(receipt_handle)
        return

    event_id = _parse_uuid(body.get("event_id"))
    if not event_id:
        logger.error("Invalid event_id in event notification SQS message: %s", body)
        if receipt_handle:
            delete_event_notification_message(receipt_handle)
        return

    if not get_bool("NOTIFICATION_DISPATCH_ENABLED"):
        logger.info("Event notification dispatch disabled; deleting event for %s", event_id)
        if receipt_handle:
            delete_event_notification_message(receipt_handle)
        return

    event_type = body.get("event_type")
    if event_type == EVENT_REMINDER_EVENT:
        reminder_type = body.get("reminder_type")
        fire_at = body.get("fire_at")
        await _process_event_reminder(event_id, reminder_type, fire_at, receipt_handle)
        return

    if event_type == EVENT_ANNOUNCEMENT_EVENT:
        await _process_event_announcement(
            event_id,
            body.get("announcement_id"),
            body.get("audience"),
            body.get("title"),
            body.get("body"),
            receipt_handle,
        )
        return

    assert event_type == EVENT_CREATED_EVENT
    await _process_event_created(event_id, receipt_handle)


async def _process_event_announcement(
    event_id: UUID,
    announcement_id: str,
    audience: str,
    title: str,
    body: str,
    receipt_handle: Optional[str],
) -> None:
    try:
        targets = await _fetch_all_announcement_targets(event_id, audience)
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.error("Event not found for announcement %s", event_id)
            if receipt_handle:
                delete_event_notification_message(receipt_handle)
            return
        raise TransientEventNotificationError(str(exc.detail)) from exc

    devices = [
        device
        for recipient in targets.recipients
        for device in recipient.push_devices
    ]
    if not devices:
        # Also the shape of a suppressed announcement: the backend returns no
        # recipients when the event's notifications switch went off after the
        # organizer queued it.
        logger.info(
            "No push devices for announcement %s on event %s; deleting message",
            announcement_id,
            event_id,
        )
        if receipt_handle:
            delete_event_notification_message(receipt_handle)
        return

    concurrency = max(get_int("EVENT_NOTIFICATION_SEND_CONCURRENCY"), 1)
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *[
            _send_announcement_to_device(
                event_id=event_id,
                announcement_id=announcement_id,
                title=title,
                body=body,
                device=device,
                semaphore=semaphore,
            )
            for device in devices
        ]
    )

    sent = results.count("sent")
    skipped = results.count("skipped")
    permanent_failed = results.count("permanent_failed")
    transient_failed = results.count("transient_failed")
    logger.info(
        "Announcement %s for event %s processed: sent=%s permanent_failed=%s "
        "transient_failed=%s skipped=%s",
        announcement_id,
        event_id,
        sent,
        permanent_failed,
        transient_failed,
        skipped,
    )

    if transient_failed:
        raise TransientEventNotificationError(
            f"{transient_failed} device(s) failed transiently"
        )
    if receipt_handle:
        delete_event_notification_message(receipt_handle)


async def run_event_notification_sqs_consumer(stop_event: asyncio.Event) -> None:
    logger.info("Event notification SQS consumer started")
    while not stop_event.is_set():
        if not is_event_notification_sqs_poll_enabled():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_IDLE_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        try:
            messages = await asyncio.to_thread(receive_event_notification_messages)
            if not messages:
                continue
            for message in messages:
                if stop_event.is_set():
                    break
                try:
                    await process_event_notification_message(message)
                except TransientEventNotificationError:
                    logger.warning(
                        "Leaving event notification SQS message for retry: %s",
                        message.get("MessageId"),
                    )
                except Exception:
                    logger.exception("Unexpected event notification consumer error")
        except Exception:
            logger.exception("Event notification SQS consumer loop error")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_ERROR_SECONDS)
            except asyncio.TimeoutError:
                pass

    logger.info("Event notification SQS consumer stopped")
