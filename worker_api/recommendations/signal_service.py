from datetime import datetime, timezone
from typing import Sequence, Set
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from worker_api.recommendations.models import (
    RecDismissal,
    RecExposure,
    RecMembership,
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


def _apply(db: Session, event: RecommendationEvent) -> None:
    occurred_at = _aware(event.occurred_at)
    if event.action in MEMBERSHIP_ADD_ACTIONS:
        if db.get(RecMembership, _key(event)) is None:
            db.add(RecMembership(**_key(event), joined_at=occurred_at))
    elif event.action in MEMBERSHIP_REMOVE_ACTIONS:
        membership = db.get(RecMembership, _key(event))
        if membership is not None:
            db.delete(membership)
    elif event.action == ACTION_DISMISS:
        if db.get(RecDismissal, _key(event)) is None:
            db.add(RecDismissal(**_key(event), dismissed_at=occurred_at))
    elif event.action in (ACTION_IMPRESSION, ACTION_CLICK):
        exposure = db.get(RecExposure, _key(event))
        if exposure is None:
            exposure = RecExposure(**_key(event), impressions=0, clicks=0, last_seen_at=occurred_at)
            db.add(exposure)
        if event.action == ACTION_IMPRESSION:
            exposure.impressions += 1
        else:
            exposure.clicks += 1
        exposure.last_seen_at = max(_aware(exposure.last_seen_at), occurred_at)


def apply_events(db: Session, events: Sequence[RecommendationEvent]) -> Set[UUID]:
    """Fold events into the signal tables in one transaction.

    Returns the users whose suggestions should be recomputed. Events already
    applied (an SQS redelivery, a backend reconcile resend) are skipped, so
    counts stay exact."""
    if not events:
        return set()

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
