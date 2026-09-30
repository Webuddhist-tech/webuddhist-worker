import logging

from worker_api.config import get_bool
from worker_api.notifications.schemas import DispatchRoutineNotificationsResponse
from worker_api.notifications.services.push.config_loader import is_push_configured
from worker_api.notifications.services.push.fcm_client import (
    send_routine_push_notification,
)
from worker_api.notifications.services.routine_notification_service import (
    get_routine_notification_targets,
)

logger = logging.getLogger(__name__)


async def dispatch_routine_notifications_service() -> DispatchRoutineNotificationsResponse:
    targets = await get_routine_notification_targets()

    if not get_bool("NOTIFICATION_DISPATCH_ENABLED"):
        return DispatchRoutineNotificationsResponse(
            generated_at=targets.generated_at,
            matched_time_utc=targets.matched_time_utc,
            groups=targets.groups,
            processed=0,
            sent=0,
            failed=0,
            skipped=0,
        )

    processed = 0
    sent = 0
    failed = 0
    skipped = 0

    for group in targets.groups:
        for user in group.users:
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
                    await send_routine_push_notification(
                        device_token=device.token,
                        session_type=group.session_type,
                        source_id=group.source_id,
                        title=notification.title,
                        body=notification.body,
                        image_url=notification.image_url,
                    )
                    sent += 1
                except Exception:
                    logger.exception(
                        "Failed to dispatch routine notification to user %s",
                        user.user_id,
                    )
                    failed += 1

    return DispatchRoutineNotificationsResponse(
        generated_at=targets.generated_at,
        matched_time_utc=targets.matched_time_utc,
        groups=targets.groups,
        processed=processed,
        sent=sent,
        failed=failed,
        skipped=skipped,
    )
