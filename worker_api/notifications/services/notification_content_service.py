import logging

from worker_api.config import get, get_random_default_notification_body
from worker_api.notifications.models.reminder_models import UpcomingReminder
from worker_api.notifications.schemas import NotificationContent

logger = logging.getLogger(__name__)


def build_notification_content(reminder: UpcomingReminder) -> tuple[str, str]:
    routine = reminder.routine_config or {}
    title = get("NOTIFICATION_DEFAULT_TITLE")
    body = get_random_default_notification_body()

    if routine.get("message_template"):
        body = routine["message_template"]
    elif routine.get("plan_name"):
        body = f"Time for {routine['plan_name']}"
    elif routine.get("current_day_number") is not None:
        body = f"Day {routine['current_day_number']}: time for your practice."

    return title, body


async def resolve_notification_content(reminder: UpcomingReminder) -> NotificationContent:
    from worker_api.notifications.services.backend_client import fetch_plan_notification_content

    try:
        return await fetch_plan_notification_content(
            user_id=reminder.user_id,
            plan_id=reminder.plan_id,
        )
    except Exception:
        logger.exception(
            "Failed to fetch plan notification content from backend for user %s plan %s",
            reminder.user_id,
            reminder.plan_id,
        )
        title, body = build_notification_content(reminder)
        return NotificationContent(title=title, body=body, image_url=None)
