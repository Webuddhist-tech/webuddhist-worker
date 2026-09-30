"""Read-only SQLAlchemy models for backend routine and notification tables."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from worker_api.db.database import Base


class Routine(Base):
    __tablename__ = "routines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True))
    deleted_at = Column(DateTime(timezone=True))


class RoutineTimeBlock(Base):
    __tablename__ = "routine_time_blocks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    routine_id = Column(
        UUID(as_uuid=True),
        ForeignKey("routines.id", ondelete="CASCADE"),
        nullable=False,
    )
    time = Column(String(5), nullable=False)
    time_int = Column(Integer, nullable=False)
    notification_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True))
    deleted_at = Column(DateTime(timezone=True))


class RoutineSession(Base):
    __tablename__ = "routine_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    time_block_id = Column(
        UUID(as_uuid=True),
        ForeignKey("routine_time_blocks.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_type = Column(String(32), nullable=False)
    source_id = Column(UUID(as_uuid=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    display_order = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True))


class PushDeviceToken(Base):
    __tablename__ = "push_device_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(512), nullable=False)
    platform = Column(String(16), nullable=False)
    device_id = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(DateTime(timezone=True), nullable=False)


class Plan(Base):
    __tablename__ = "plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    title = Column(String(255), nullable=False)
    image_url = Column(String(1000), nullable=True)


class Series(Base):
    __tablename__ = "series"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    image = Column(String(1000), nullable=True)


class SeriesMetadata(Base):
    __tablename__ = "series_metadata"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    title = Column(String(255), nullable=False)
    series_id = Column(UUID(as_uuid=True), ForeignKey("series.id", ondelete="CASCADE"), nullable=False)


class UserPlanProgress(Base):
    __tablename__ = "user_plan_progress"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)


class DayNotification(Base):
    __tablename__ = "day_notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    day_id = Column(UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    image_type = Column(String(16), nullable=True)
    image_url = Column(String(1000), nullable=True)


class RecitationCollection(Base):
    __tablename__ = "recitation_collections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    name = Column(String(255), nullable=False)
    img_url = Column(String(1000), nullable=False)
