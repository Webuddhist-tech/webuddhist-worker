"""The worker's own copy of the recommendation signals.

Rebuilt from the backend on every full refresh and kept current in between
by the rec_events queue. Nothing here is the source of truth: losing it costs
one full refresh."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Index, Integer, String, Uuid

from worker_api.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RecMembership(Base):
    """A user's joined space, enrolled plan or followed tradition."""

    __tablename__ = "rec_memberships"

    user_id = Column(Uuid, primary_key=True)
    item_type = Column(String(32), primary_key=True)
    item_id = Column(Uuid, primary_key=True)
    joined_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("idx_rec_memberships_item", "item_type", "item_id"),
        Index("idx_rec_memberships_joined_at", "item_type", "joined_at"),
    )


class RecItem(Base):
    """A suggestible space or plan, with what scoring matches on."""

    __tablename__ = "rec_items"

    item_type = Column(String(32), primary_key=True)
    item_id = Column(Uuid, primary_key=True)
    group_id = Column(Uuid, nullable=True)
    tradition_id = Column(Uuid, nullable=True)
    language = Column(String(16), nullable=True)
    tag_ids = Column(JSON, nullable=False, default=list)
    published_at = Column(DateTime(timezone=True), nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class RecExposure(Base):
    """How often a suggestion was shown to and tapped by a user."""

    __tablename__ = "rec_exposures"

    user_id = Column(Uuid, primary_key=True)
    item_type = Column(String(32), primary_key=True)
    item_id = Column(Uuid, primary_key=True)
    impressions = Column(Integer, nullable=False, default=0)
    clicks = Column(Integer, nullable=False, default=0)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class RecDismissal(Base):
    __tablename__ = "rec_dismissals"

    user_id = Column(Uuid, primary_key=True)
    item_type = Column(String(32), primary_key=True)
    item_id = Column(Uuid, primary_key=True)
    dismissed_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class RecProcessedEvent(Base):
    """Event ids already applied, so an SQS redelivery or a reconcile resend
    does not count an impression twice."""

    __tablename__ = "rec_processed_events"

    event_id = Column(Uuid, primary_key=True)
    processed_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("idx_rec_processed_events_processed_at", "processed_at"),)
