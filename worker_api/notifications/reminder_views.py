from uuid import UUID

from fastapi import APIRouter, HTTPException
from starlette import status

from worker_api.notifications.schemas import (
    EnrollReminderRequest,
    ReminderResponse,
    UpdateReminderRequest,
)
from worker_api.notifications.services.reminder_enroll_service import (
    cancel_reminder_service,
    enroll_reminder_service,
    update_reminder_service,
)

reminder_router = APIRouter(prefix="/notifications", tags=["Notifications"])


@reminder_router.post("/reminders", status_code=status.HTTP_201_CREATED)
async def enroll_reminder(request: EnrollReminderRequest) -> ReminderResponse:
    return enroll_reminder_service(request)


@reminder_router.put("/reminders/{user_id}/{plan_id}", status_code=status.HTTP_200_OK)
async def update_reminder(
    user_id: UUID,
    plan_id: UUID,
    request: UpdateReminderRequest,
) -> ReminderResponse:
    try:
        return update_reminder_service(user_id, plan_id, request)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "NOT_FOUND", "message": str(exc)},
        ) from exc


@reminder_router.delete("/reminders/{user_id}/{plan_id}", status_code=status.HTTP_200_OK)
async def cancel_reminder(user_id: UUID, plan_id: UUID) -> dict:
    return cancel_reminder_service(user_id, plan_id)
