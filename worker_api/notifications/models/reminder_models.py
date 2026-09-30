from datetime import datetime, timezone
from uuid import uuid4

import _datetime
from sqlalchemy import Column, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from worker_api.db.database import Base
from worker_api.notifications.enums import PushPlatform, ReminderStatus


class UpcomingReminder(Base):
    __tablename__ = "upcoming_reminders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    plan_id = Column(UUID(as_uuid=True), nullable=False)
    trigger_at = Column(DateTime(timezone=True), nullable=False)
    timezone = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default=ReminderStatus.PENDING)
    device_token = Column(Text, nullable=False)
    platform = Column(String(16), nullable=False)
    routine_config = Column(JSONB, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=datetime.now(_datetime.timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now(_datetime.timezone.utc),
        onupdate=datetime.now(_datetime.timezone.utc),
    )

    __table_args__ = (
        Index(
            "idx_upcoming_reminders_due",
            "trigger_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("idx_upcoming_reminders_user_plan", "user_id", "plan_id"),
    )
