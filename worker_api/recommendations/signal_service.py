from datetime import datetime, timezone
from typing import Sequence, Set
from uuid import UUID

from sqlalchemy import case, delete, select, text, update
from sqlalchemy.orm import Session

from worker_api.recommendations.models import (
    RecDismissal,
    RecExposure,
    RecMembership,
    RecMembershipChange,
    RecProcessedEvent,
)
from worker_api.recommendations.schemas import (
    ACTION_CLICK,
    ACTION_DISMISS,
    ACTION_IMPRESSION,
    MEMBERSHIP_ADD_ACTIONS,
    MEMBERSHIP_REMOVE_ACTIONS,
    RESCORE_ACTIONS,
    RecommendationEvent,
)


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _key(event: RecommendationEvent) -> dict:
    return {"user_id": event.user_id, "item_type": event.item_type, "item_id": event.item_id}


_MEMBERSHIP_ACTIONS = MEMBERSHIP_ADD_ACTIONS | MEMBERSHIP_REMOVE_ACTIONS
# Arbitrary constant shared by every worker for the membership advisory lock.
_MEMBERSHIP_LOCK_ID = 7_301_142


def lock_memberships(db: Session) -> None:
    """Serialize membership writes between the event path and the resync
    until this transaction ends, so neither decides on rows the other is
    about to change. Only Postgres has the lock; SQLite runs one writer."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _MEMBERSHIP_LOCK_ID})


def record_membership_change(
    db: Session,
    key: dict,
    *,
    present: bool,
    changed_at: datetime,
    joined_at: datetime | None = None,
) -> bool:
    """Set the membership in ``key`` to ``present`` unless a change at or
    after ``changed_at`` is already recorded. False when the change is stale.
    Callers hold :func:`lock_memberships`."""
    change = db.get(RecMembershipChange, key)
    if change is not None and _aware(change.changed_at) >= changed_at:
        return False
    if change is None:
        db.add(RecMembershipChange(**key, changed_at=changed_at, present=present))
    else:
        change.changed_at = changed_at
        change.present = present

    membership = db.get(RecMembership, key)
    if present and membership is None:
        db.add(RecMembership(**key, joined_at=joined_at or changed_at))
    elif not present and membership is not None:
        db.delete(membership)
    return True


def _record_exposure(db: Session, event: RecommendationEvent, occurred_at: datetime) -> None:
    """Increment in the database, so concurrent workers never lose a count.
    Two workers inserting the same new row conflict on the key, and the
    loser's messages are retried into the update."""
    column = "impressions" if event.action == ACTION_IMPRESSION else "clicks"
    key = _key(event)
    result = db.execute(
        update(RecExposure)
        .where(*(getattr(RecExposure, name) == value for name, value in key.items()))
        .values(
            {
                column: getattr(RecExposure, column) + 1,
                "last_seen_at": case(
                    (RecExposure.last_seen_at < occurred_at, occurred_at),
                    else_=RecExposure.last_seen_at,
                ),
            }
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        counts = {"impressions": 0, "clicks": 0, column: 1}
        db.add(RecExposure(**key, **counts, last_seen_at=occurred_at))


def _apply(db: Session, event: RecommendationEvent) -> None:
    occurred_at = _aware(event.occurred_at)
    if event.action in MEMBERSHIP_ADD_ACTIONS:
        record_membership_change(db, _key(event), present=True, changed_at=occurred_at)
    elif event.action in MEMBERSHIP_REMOVE_ACTIONS:
        record_membership_change(db, _key(event), present=False, changed_at=occurred_at)
    elif event.action == ACTION_DISMISS:
        if db.get(RecDismissal, _key(event)) is None:
            db.add(RecDismissal(**_key(event), dismissed_at=occurred_at))
    elif event.action in (ACTION_IMPRESSION, ACTION_CLICK):
        _record_exposure(db, event, occurred_at)


def apply_events(db: Session, events: Sequence[RecommendationEvent]) -> Set[UUID]:
    """Fold events into the signal tables in one transaction.

    Returns the users whose suggestions should be recomputed. Events already
    applied (an SQS redelivery, a backend reconcile resend) are skipped, so
    counts stay exact. A membership event older than the last recorded
    change to that membership is ignored, so out-of-order delivery across
    polls cannot resurrect a membership."""
    if not events:
        return set()
    if any(event.action in _MEMBERSHIP_ACTIONS for event in events):
        lock_memberships(db)

    seen_ids = set(
        db.execute(
            select(RecProcessedEvent.event_id).where(
                RecProcessedEvent.event_id.in_([event.event_id for event in events])
            )
        ).scalars()
    )
    rescore: Set[UUID] = set()
    for event in sorted(events, key=lambda e: _aware(e.occurred_at)):
        if event.event_id in seen_ids:
            continue
        seen_ids.add(event.event_id)
        _apply(db, event)
        db.add(RecProcessedEvent(event_id=event.event_id))
        db.flush()
        if event.action in RESCORE_ACTIONS:
            rescore.add(event.user_id)
    db.commit()
    return rescore


def purge_processed_events(db: Session, *, older_than: datetime) -> int:
    """Forget event ids past any redelivery window, so the table stays small."""
    result = db.execute(
        delete(RecProcessedEvent).where(RecProcessedEvent.processed_at < older_than)
    )
    db.commit()
    return result.rowcount or 0
