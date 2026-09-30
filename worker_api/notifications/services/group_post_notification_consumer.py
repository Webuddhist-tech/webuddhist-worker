import asyncio
import logging
from typing import Any, Dict, Optional
from uuid import UUID

import httpx
import redis
from fastapi import HTTPException

from worker_api.config import get, get_bool, get_int
from worker_api.notifications.group_post_sqs_client import (
    delete_group_post_notification_message,
    is_group_post_notification_sqs_poll_enabled,
    parse_group_post_notification_message_body,
    receive_group_post_notification_messages,
)
from worker_api.notifications.schemas import (
    GroupPostNotificationTargetsResponse,
    GroupPostPushDeviceTarget,
)
from worker_api.notifications.services.backend_client import (
    deactivate_push_device,
    fetch_group_post_notification_targets,
)
from worker_api.notifications.services.push.config_loader import is_push_configured
from worker_api.notifications.services.push.fcm_client import (
    PermanentPushTokenError,
    send_group_post_push_notification,
)

logger = logging.getLogger(__name__)

_POLL_IDLE_SECONDS = 5
_POLL_ERROR_SECONDS = 10
_redis_client: redis.Redis | None = None


class TransientGroupPostNotificationError(Exception):
    """Raised when processing should leave the SQS message for retry."""


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(get("CACHE_CONNECTION_STRING"))
    return _redis_client


def _idempotency_key(*, post_id: UUID, push_device_id: UUID) -> str:
    prefix = get("GROUP_POST_NOTIFICATION_IDEMPOTENCY_KEY_PREFIX")
    return f"{prefix}{post_id}:{push_device_id}"


def _already_sent(*, post_id: UUID, push_device_id: UUID) -> bool:
    client = _get_redis_client()
    return bool(client.exists(_idempotency_key(post_id=post_id, push_device_id=push_device_id)))


def _mark_sent(*, post_id: UUID, push_device_id: UUID) -> None:
    client = _get_redis_client()
    client.setex(
        _idempotency_key(post_id=post_id, push_device_id=push_device_id),
        get_int("GROUP_POST_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS"),
        "1",
    )


def _parse_uuid(value: Any) -> Optional[UUID]:
    if value is None or value == "":
        return None
    return UUID(str(value))


async def _fetch_all_targets(post_id: UUID) -> GroupPostNotificationTargetsResponse:
    page_size = max(get_int("GROUP_POST_NOTIFICATION_TARGET_PAGE_SIZE"), 1)
    skip = 0
    first_page: GroupPostNotificationTargetsResponse | None = None
    all_recipients = []

    while True:
        try:
            page = await fetch_group_post_notification_targets(
                post_id=post_id,
                skip=skip,
                limit=page_size,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Group post not found") from exc
            raise TransientGroupPostNotificationError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise TransientGroupPostNotificationError(str(exc)) from exc

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
    targets: GroupPostNotificationTargetsResponse,
    device: GroupPostPushDeviceTarget,
    semaphore: asyncio.Semaphore,
) -> str:
    """Return sent | skipped | permanent_failed | transient_failed."""
    async with semaphore:
        if not is_push_configured(device.platform):
            return "skipped"

        if _already_sent(post_id=targets.post_id, push_device_id=device.id):
            return "skipped"

        try:
            await send_group_post_push_notification(
                device_token=device.token,
                post_id=targets.post_id,
                group_id=targets.group_id,
                author_id=targets.author_id,
                title=targets.title,
                body=targets.body,
            )
            _mark_sent(post_id=targets.post_id, push_device_id=device.id)
            return "sent"
        except PermanentPushTokenError:
            logger.warning(
                "Deactivating permanently invalid push device %s for group post %s",
                device.id,
                targets.post_id,
            )
            try:
                await deactivate_push_device(push_device_id=device.id)
            except Exception:
                logger.exception("Failed to deactivate push device %s", device.id)
            _mark_sent(post_id=targets.post_id, push_device_id=device.id)
            return "permanent_failed"
        except Exception:
            logger.exception(
                "Transient FCM failure for device %s on group post %s",
                device.id,
                targets.post_id,
            )
            return "transient_failed"


async def process_group_post_notification_message(message: Dict[str, Any]) -> None:
    receipt_handle = message.get("ReceiptHandle")
    body = parse_group_post_notification_message_body(message.get("Body", ""))
    if not body:
        if receipt_handle:
            delete_group_post_notification_message(receipt_handle)
        return

    post_id = _parse_uuid(body.get("post_id"))
    if not post_id:
        logger.error("Invalid post_id in group post notification SQS message: %s", body)
        if receipt_handle:
            delete_group_post_notification_message(receipt_handle)
        return

    if not get_bool("NOTIFICATION_DISPATCH_ENABLED"):
        logger.info("Group post notification dispatch disabled; deleting event for %s", post_id)
        if receipt_handle:
            delete_group_post_notification_message(receipt_handle)
        return

    try:
        targets = await _fetch_all_targets(post_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.error("Group post not found for notification event %s", post_id)
            if receipt_handle:
                delete_group_post_notification_message(receipt_handle)
            return
        raise TransientGroupPostNotificationError(str(exc.detail)) from exc

    devices = [
        device
        for recipient in targets.recipients
        for device in recipient.push_devices
    ]
    if not devices:
        logger.info("No push devices for group post %s; deleting event", post_id)
        if receipt_handle:
            delete_group_post_notification_message(receipt_handle)
        return

    concurrency = max(get_int("GROUP_POST_NOTIFICATION_SEND_CONCURRENCY"), 1)
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
        "Group post notification %s processed: sent=%s permanent_failed=%s transient_failed=%s skipped=%s",
        post_id,
        sent,
        permanent_failed,
        transient_failed,
        skipped,
    )

    if transient_failed > 0:
        raise TransientGroupPostNotificationError(
            f"Transient failures remain for group post {post_id}"
        )

    if receipt_handle:
        delete_group_post_notification_message(receipt_handle)


async def run_group_post_notification_sqs_consumer(stop_event: asyncio.Event) -> None:
    logger.info("Group post notification SQS consumer started")
    while not stop_event.is_set():
        if not is_group_post_notification_sqs_poll_enabled():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_IDLE_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        try:
            messages = await asyncio.to_thread(receive_group_post_notification_messages)
            if not messages:
                continue
            for message in messages:
                if stop_event.is_set():
                    break
                try:
                    await process_group_post_notification_message(message)
                except TransientGroupPostNotificationError:
                    logger.warning(
                        "Leaving group post notification SQS message for retry: %s",
                        message.get("MessageId"),
                    )
                except Exception:
                    logger.exception("Unexpected group post notification consumer error")
        except Exception:
            logger.exception("Group post notification SQS consumer loop error")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_ERROR_SECONDS)
            except asyncio.TimeoutError:
                pass

    logger.info("Group post notification SQS consumer stopped")
