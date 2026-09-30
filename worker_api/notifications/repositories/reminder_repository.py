from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from worker_api.notifications.enums import ReminderStatus
from worker_api.notifications.models.reminder_models import UpcomingReminder


def create_reminder(
    db: Session,
    *,
    user_id: UUID,
    plan_id: UUID,
    trigger_at: datetime,
    timezone_name: str,
    device_token: str,
    platform: str,
    routine_config: dict,
) -> UpcomingReminder:
    reminder = UpcomingReminder(
        user_id=user_id,
        plan_id=plan_id,
        trigger_at=trigger_at,
        timezone=timezone_name,
        status=ReminderStatus.PENDING,
        device_token=device_token,
        platform=platform,
        routine_config=routine_config,
    )
    db.add(reminder)
    db.flush()
    return reminder


def get_due_reminders(
    db: Session,
    *,
    now: datetime,
    limit: int,
) -> list[UpcomingReminder]:
    stmt = (
        select(UpcomingReminder)
        .where(
            UpcomingReminder.status == ReminderStatus.PENDING,
            UpcomingReminder.trigger_at <= now,
        )
        .order_by(UpcomingReminder.trigger_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(db.scalars(stmt).all())


def mark_sent(db: Session, reminder: UpcomingReminder) -> None:
    reminder.status = ReminderStatus.SENT
    reminder.updated_at = datetime.now(timezone.utc)


def cancel_pending_for_user_plan(
    db: Session,
    *,
    user_id: UUID,
    plan_id: UUID,
) -> int:
    pending = (
        db.query(UpcomingReminder)
        .filter(
            UpcomingReminder.user_id == user_id,
            UpcomingReminder.plan_id == plan_id,
            UpcomingReminder.status == ReminderStatus.PENDING,
        )
        .all()
    )
    for reminder in pending:
        reminder.status = ReminderStatus.CANCELLED
        reminder.updated_at = datetime.now(timezone.utc)
    return len(pending)


def get_pending_for_user_plan(
    db: Session,
    *,
    user_id: UUID,
    plan_id: UUID,
) -> UpcomingReminder | None:
    return (
        db.query(UpcomingReminder)
        .filter(
            UpcomingReminder.user_id == user_id,
            UpcomingReminder.plan_id == plan_id,
            UpcomingReminder.status == ReminderStatus.PENDING,
        )
        .order_by(UpcomingReminder.trigger_at)
        .first()
    )
