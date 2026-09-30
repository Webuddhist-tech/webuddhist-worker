from worker_api.notifications.schemas import VerseOfDayNotificationTargetsResponse
from worker_api.notifications.services.backend_client import fetch_verse_of_day_notification_targets


async def get_verse_of_day_notification_targets() -> VerseOfDayNotificationTargetsResponse:
    return await fetch_verse_of_day_notification_targets()
