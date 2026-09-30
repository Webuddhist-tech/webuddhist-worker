import logging
from datetime import datetime, timezone
from uuid import UUID

import redis

from worker_api.config import get, get_bool, get_int
from worker_api.notifications.enums import PushPlatform
from worker_api.notifications.models.reminder_models import UpcomingReminder
from worker_api.notifications.services.push.config_loader import is_push_configured
from worker_api.notifications.services.push.fcm_client import (
    build_routine_notification_data,
    send_fcm_notification,
)

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(get("CACHE_CONNECTION_STRING"))
    return _redis_client


def _idempotency_key(reminder_id: UUID) -> str:
    prefix = get("NOTIFICATION_IDEMPOTENCY_KEY_PREFIX")
    return f"{prefix}{reminder_id}"


def _already_dispatched(reminder_id: UUID) -> bool:
    if not get_bool("NOTIFICATION_IDEMPOTENCY_ENABLED"):
        return False
    client = _get_redis_client()
    return bool(client.exists(_idempotency_key(reminder_id)))


def _mark_dispatched(reminder_id: UUID) -> None:
    if not get_bool("NOTIFICATION_IDEMPOTENCY_ENABLED"):
        return
    client = _get_redis_client()
    client.setex(
        _idempotency_key(reminder_id),
        get_int("NOTIFICATION_IDEMPOTENCY_TTL_SECONDS"),
        "1",
    )


async def send_push_notification(
    reminder: UpcomingReminder,
    title: str,
    body: str,
    image_url: str | None = None,
) -> None:
    if not is_push_configured(reminder.platform):
        logger.warning(
            "Push not configured for platform %s; skipping send for reminder %s",
            reminder.platform,
            reminder.id,
        )
        return

    if reminder.platform in (PushPlatform.ANDROID, PushPlatform.IOS):
        await send_fcm_notification(
            device_token=reminder.device_token,
            title=title,
            body=body,
            image_url=image_url,
            data=build_routine_notification_data(
                session_type="PLAN",
                source_id=reminder.plan_id,
                title=title,
                body=body,
                image_url=image_url,
            ),
        )
        return

    raise ValueError(f"Unsupported platform: {reminder.platform}")
