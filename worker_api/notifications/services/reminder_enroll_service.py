from uuid import UUID

from worker_api.db.database import SessionLocal
from worker_api.notifications.repositories import reminder_repository
from worker_api.notifications.schemas import EnrollReminderRequest, ReminderResponse, UpdateReminderRequest
from worker_api.notifications.services.reminder_schedule_service import compute_next_trigger_at


def enroll_reminder_service(request: EnrollReminderRequest) -> ReminderResponse:
    routine_config = request.routine.model_dump(exclude_none=True)

    with SessionLocal() as db:
        reminder_repository.cancel_pending_for_user_plan(
            db,
            user_id=request.user_id,
            plan_id=request.plan_id,
        )
        reminder = reminder_repository.create_reminder(
            db,
            user_id=request.user_id,
            plan_id=request.plan_id,
            trigger_at=compute_next_trigger_at(routine_config, request.timezone),
            timezone_name=request.timezone,
            device_token=request.device_token,
            platform=request.platform.value,
            routine_config=routine_config,
        )
        db.commit()
        db.refresh(reminder)
        return ReminderResponse.model_validate(reminder)


def update_reminder_service(
    user_id: UUID,
    plan_id: UUID,
    request: UpdateReminderRequest,
) -> ReminderResponse:
    with SessionLocal() as db:
        existing = reminder_repository.get_pending_for_user_plan(
            db,
            user_id=user_id,
            plan_id=plan_id,
        )
        if not existing:
            raise ValueError("No pending reminder found for this user and plan")

        timezone_name = request.timezone or existing.timezone
        device_token = request.device_token or existing.device_token
        platform = request.platform.value if request.platform else existing.platform
        routine_config = (
            request.routine.model_dump(exclude_none=True)
            if request.routine
            else existing.routine_config
        )

        reminder_repository.cancel_pending_for_user_plan(db, user_id=user_id, plan_id=plan_id)
        reminder = reminder_repository.create_reminder(
            db,
            user_id=user_id,
            plan_id=plan_id,
            trigger_at=compute_next_trigger_at(routine_config, timezone_name),
            timezone_name=timezone_name,
            device_token=device_token,
            platform=platform,
            routine_config=routine_config,
        )
        db.commit()
        db.refresh(reminder)
        return ReminderResponse.model_validate(reminder)


def cancel_reminder_service(user_id: UUID, plan_id: UUID) -> dict:
    with SessionLocal() as db:
        cancelled = reminder_repository.cancel_pending_for_user_plan(
            db,
            user_id=user_id,
            plan_id=plan_id,
        )
        db.commit()
        return {"cancelled": cancelled}
