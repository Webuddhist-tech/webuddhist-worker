from worker_api.notifications.schemas import RoutineNotificationTargetsResponse
from worker_api.notifications.services.backend_client import fetch_routine_notification_targets


async def get_routine_notification_targets() -> RoutineNotificationTargetsResponse:
    return await fetch_routine_notification_targets()
