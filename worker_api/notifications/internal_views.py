from fastapi import APIRouter, Depends, HTTPException
from starlette import status

from worker_api.notifications.dependencies import verify_dispatch_token
from worker_api.notifications.schemas import (
    DispatchDueNotificationsResponse,
    DispatchRoutineNotificationsResponse,
    DispatchVerseOfDayNotificationsResponse,
    RoutineNotificationTargetsResponse,
    SendTestNotificationRequest,
    SendTestNotificationResponse,
)
from worker_api.notifications.services.dispatch_service import dispatch_due_notifications_service
from worker_api.notifications.services.routine_dispatch_service import (
    dispatch_routine_notifications_service,
)
from worker_api.notifications.services.routine_notification_service import (
    get_routine_notification_targets,
)
from worker_api.notifications.services.send_test_notification_service import (
    send_test_notification_service,
)
from worker_api.notifications.services.verse_of_day_dispatch_service import (
    dispatch_verse_of_day_notifications_service,
)

internal_router = APIRouter(prefix="/internal", tags=["Internal"])


@internal_router.post(
    "/dispatch-due-notifications",
    status_code=status.HTTP_200_OK,
)
async def dispatch_due_notifications(
    _: None = Depends(verify_dispatch_token),
) -> DispatchDueNotificationsResponse:
    return await dispatch_due_notifications_service()


@internal_router.get(
    "/routine-notification-targets",
    status_code=status.HTTP_200_OK,
)
async def routine_notification_targets(
    _: None = Depends(verify_dispatch_token),
) -> RoutineNotificationTargetsResponse:
    return await get_routine_notification_targets()


@internal_router.post(
    "/dispatch-routine-notifications",
    status_code=status.HTTP_200_OK,
)
async def dispatch_routine_notifications(
    _: None = Depends(verify_dispatch_token),
) -> DispatchRoutineNotificationsResponse:
    return await dispatch_routine_notifications_service()


@internal_router.post(
    "/dispatch-verse-of-day-notifications",
    status_code=status.HTTP_200_OK,
)
async def dispatch_verse_of_day_notifications(
    _: None = Depends(verify_dispatch_token),
) -> DispatchVerseOfDayNotificationsResponse:
    return await dispatch_verse_of_day_notifications_service()


@internal_router.post(
    "/send-test-notification",
    status_code=status.HTTP_200_OK,
)
async def send_test_notification(
    request: SendTestNotificationRequest,
    _: None = Depends(verify_dispatch_token),
) -> SendTestNotificationResponse:
    try:
        return await send_test_notification_service(request)
    except ValueError as exc:
        message = str(exc)
        if "not configured" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "NOT_CONFIGURED", "message": message},
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "NOT_FOUND", "message": message},
        ) from exc
