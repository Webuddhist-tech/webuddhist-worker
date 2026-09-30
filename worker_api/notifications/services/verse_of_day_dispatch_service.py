import logging

from worker_api.config import get_bool
from worker_api.notifications.schemas import DispatchVerseOfDayNotificationsResponse
from worker_api.notifications.services.push.config_loader import is_push_configured
from worker_api.notifications.services.push.fcm_client import (
    send_verse_of_day_push_notification,
)
from worker_api.notifications.services.verse_of_day_notification_service import (
    get_verse_of_day_notification_targets,
)

logger = logging.getLogger(__name__)


async def dispatch_verse_of_day_notifications_service() -> DispatchVerseOfDayNotificationsResponse:
    targets = await get_verse_of_day_notification_targets()

    if not get_bool("NOTIFICATION_DISPATCH_ENABLED"):
        return DispatchVerseOfDayNotificationsResponse(
            generated_at=targets.generated_at,
            users=targets.users,
            processed=0,
            sent=0,
            failed=0,
            skipped=0,
        )

    processed = 0
    sent = 0
    failed = 0
    skipped = 0

    for user in targets.users:
        notification = user.notification
        for device in user.push_devices:
            processed += 1
            if not is_push_configured(device.platform):
                logger.warning(
                    "Push not configured for platform %s; skipping user %s",
                    device.platform,
                    user.user_id,
                )
                skipped += 1
                continue

            try:
                await send_verse_of_day_push_notification(
                    device_token=device.token,
                    title=notification.title,
                    body=notification.body,
                    image_url=notification.image_url,
                )
                sent += 1
            except Exception:
                logger.exception(
                    "Failed to dispatch verse-of-day notification to user %s",
                    user.user_id,
                )
                failed += 1

    return DispatchVerseOfDayNotificationsResponse(
        generated_at=targets.generated_at,
        users=targets.users,
        processed=processed,
        sent=sent,
        failed=failed,
        skipped=skipped,
    )
