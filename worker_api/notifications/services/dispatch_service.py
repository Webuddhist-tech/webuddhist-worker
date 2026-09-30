import logging
from datetime import datetime, timezone

from worker_api.config import get_bool, get_int
from worker_api.db.database import SessionLocal
from worker_api.notifications.repositories import reminder_repository
from worker_api.notifications.schemas import DispatchDueNotificationsResponse
from worker_api.notifications.services.notification_content_service import resolve_notification_content
from worker_api.notifications.services.push_service import (
    _already_dispatched,
    _mark_dispatched,
    send_push_notification,
)
from worker_api.notifications.services.reminder_schedule_service import compute_next_trigger_at

logger = logging.getLogger(__name__)


async def dispatch_due_notifications_service() -> DispatchDueNotificationsResponse:
    if not get_bool("NOTIFICATION_DISPATCH_ENABLED"):
        return DispatchDueNotificationsResponse(processed=0, sent=0, failed=0, skipped=0)

    now = datetime.now(timezone.utc)
    batch_size = get_int("NOTIFICATION_DISPATCH_BATCH_SIZE")
    processed = 0
    sent = 0
    failed = 0
    skipped = 0

    with SessionLocal() as db:
        due_reminders = reminder_repository.get_due_reminders(db, now=now, limit=batch_size)

        for reminder in due_reminders:
            processed += 1
            if _already_dispatched(reminder.id):
                skipped += 1
                continue

            content = await resolve_notification_content(reminder)
            try:
                await send_push_notification(
                    reminder,
                    content.title,
                    content.body,
                    content.image_url,
                )
                reminder_repository.mark_sent(db, reminder)
                reminder_repository.create_reminder(
                    db,
                    user_id=reminder.user_id,
                    plan_id=reminder.plan_id,
                    trigger_at=compute_next_trigger_at(
                        reminder.routine_config,
                        reminder.timezone,
                        after=now,
                    ),
                    timezone_name=reminder.timezone,
                    device_token=reminder.device_token,
                    platform=reminder.platform,
                    routine_config=reminder.routine_config,
                )
                _mark_dispatched(reminder.id)
                sent += 1
            except Exception:
                logger.exception("Failed to dispatch reminder %s", reminder.id)
                failed += 1

        db.commit()

    return DispatchDueNotificationsResponse(
        processed=processed,
        sent=sent,
        failed=failed,
        skipped=skipped,
    )
