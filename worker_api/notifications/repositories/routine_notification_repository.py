from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from worker_api.notifications.models.routine_models import (
    PushDeviceToken,
    Routine,
    RoutineSession,
    RoutineTimeBlock,
)


@dataclass(frozen=True)
class PushDeviceRow:
    token: str
    platform: str


def get_active_push_devices_by_email(db: Session, email: str) -> list[PushDeviceRow]:
    rows = db.execute(
        text(
            """
            SELECT pdt.token, pdt.platform
            FROM push_device_tokens pdt
            INNER JOIN users u ON u.id = pdt.user_id
            WHERE LOWER(u.email) = LOWER(:email)
              AND pdt.is_active = true
            """
        ),
        {"email": email.strip()},
    ).all()
    return [
        PushDeviceRow(token=str(row.token), platform=str(row.platform).lower())
        for row in rows
    ]


@dataclass(frozen=True)
class RoutineNotificationRow:
    user_id: UUID
    time_block_id: UUID
    time_block_time: str
    time_block_created_at: datetime
    session_type: str
    source_id: UUID | None
    device_token: str
    platform: str


def get_users_with_matching_timeblocks(
    db: Session,
    *,
    hhmm_values: list[str] | None = None,
) -> list[RoutineNotificationRow]:
    stmt = (
        select(
            Routine.user_id,
            RoutineTimeBlock.id,
            RoutineTimeBlock.time,
            RoutineTimeBlock.created_at,
            RoutineSession.session_type,
            RoutineSession.source_id,
            PushDeviceToken.token,
            PushDeviceToken.platform,
        )
        .join(Routine, RoutineTimeBlock.routine_id == Routine.id)
        .join(RoutineSession, RoutineSession.time_block_id == RoutineTimeBlock.id)
        .join(PushDeviceToken, PushDeviceToken.user_id == Routine.user_id)
        .where(
            RoutineTimeBlock.notification_enabled.is_(True),
            RoutineTimeBlock.deleted_at.is_(None),
            Routine.deleted_at.is_(None),
            PushDeviceToken.is_active.is_(True),
        )
    )

    if hhmm_values:
        stmt = stmt.where(RoutineTimeBlock.time.in_(hhmm_values))

    rows = db.execute(stmt).all()
    return [
        RoutineNotificationRow(
            user_id=row.user_id,
            time_block_id=row.id,
            time_block_time=row.time,
            time_block_created_at=row.created_at,
            session_type=str(row.session_type),
            source_id=row.source_id,
            device_token=row.token,
            platform=str(row.platform).lower(),
        )
        for row in rows
    ]


def get_plan_by_id(db: Session, plan_id: UUID):
    from worker_api.notifications.models.routine_models import Plan

    return db.get(Plan, plan_id)


def get_series_by_id(db: Session, series_id: UUID):
    from worker_api.notifications.models.routine_models import Series

    return db.get(Series, series_id)


def get_series_metadata(db: Session, series_id: UUID):
    from worker_api.notifications.models.routine_models import SeriesMetadata

    stmt = (
        select(SeriesMetadata)
        .where(SeriesMetadata.series_id == series_id)
        .limit(1)
    )
    return db.scalars(stmt).first()


def get_user_plan_progress(db: Session, *, user_id: UUID, plan_id: UUID):
    from worker_api.notifications.models.routine_models import UserPlanProgress

    stmt = select(UserPlanProgress).where(
        UserPlanProgress.user_id == user_id,
        UserPlanProgress.plan_id == plan_id,
    )
    return db.scalars(stmt).first()


def get_plan_item_by_day_number(db: Session, *, plan_id: UUID, day_number: int):
    from worker_api.audio.models.plan_items_models import PlanItem

    stmt = select(PlanItem).where(
        PlanItem.plan_id == plan_id,
        PlanItem.day_number == day_number,
    )
    return db.scalars(stmt).first()


def get_day_notification(db: Session, *, day_id: UUID):
    from worker_api.notifications.models.routine_models import DayNotification

    stmt = select(DayNotification).where(DayNotification.day_id == day_id)
    return db.scalars(stmt).first()


def get_recitation_collection(db: Session, collection_id: UUID):
    from worker_api.notifications.models.routine_models import RecitationCollection

    return db.get(RecitationCollection, collection_id)
