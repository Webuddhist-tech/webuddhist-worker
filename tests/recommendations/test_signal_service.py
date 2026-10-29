from datetime import datetime, timedelta, timezone
from uuid import uuid4

from worker_api.recommendations.models import (
    RecDismissal,
    RecExposure,
    RecMembership,
    RecProcessedEvent,
)
from worker_api.recommendations.schemas import RecommendationEvent
from worker_api.recommendations.signal_service import apply_events, purge_processed_events

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def _event(user_id, action, item_type="SPACE", item_id=None, at=NOW, event_id=None):
    return RecommendationEvent(
        event_id=event_id or uuid4(),
        user_id=user_id,
        item_type=item_type,
        item_id=item_id or uuid4(),
        action=action,
        occurred_at=at,
    )


def test_join_and_leave_update_memberships(db_session):
    user, space = uuid4(), uuid4()
    rescore = apply_events(db_session, [_event(user, "JOIN", item_id=space)])
    assert rescore == {user}
    assert db_session.query(RecMembership).count() == 1

    apply_events(db_session, [_event(user, "LEAVE", item_id=space, at=NOW + timedelta(minutes=1))])
    assert db_session.query(RecMembership).count() == 0


def test_events_apply_in_time_order_within_a_batch(db_session):
    user, space = uuid4(), uuid4()
    apply_events(
        db_session,
        [
            _event(user, "LEAVE", item_id=space, at=NOW + timedelta(seconds=5)),
            _event(user, "JOIN", item_id=space, at=NOW),
        ],
    )
    assert db_session.query(RecMembership).count() == 0


def test_redelivered_events_are_applied_once(db_session):
    user, space = uuid4(), uuid4()
    impression = _event(user, "IMPRESSION", item_id=space)
    apply_events(db_session, [impression])
    rescore = apply_events(db_session, [impression, impression])

    exposure = db_session.query(RecExposure).one()
    assert exposure.impressions == 1
    assert rescore == set()
    assert db_session.query(RecProcessedEvent).count() == 1


def test_impressions_and_clicks_do_not_trigger_rescore(db_session):
    user, plan = uuid4(), uuid4()
    rescore = apply_events(
        db_session,
        [
            _event(user, "IMPRESSION", item_type="PLAN", item_id=plan),
            _event(user, "IMPRESSION", item_type="PLAN", item_id=plan),
            _event(user, "CLICK", item_type="PLAN", item_id=plan),
        ],
    )
    exposure = db_session.query(RecExposure).one()
    assert (exposure.impressions, exposure.clicks) == (2, 1)
    assert rescore == set()


def test_dismiss_is_stored_and_rescores(db_session):
    user, space = uuid4(), uuid4()
    rescore = apply_events(db_session, [_event(user, "DISMISS", item_id=space)])
    assert rescore == {user}
    assert db_session.query(RecDismissal).one().item_id == space


def test_purge_drops_old_processed_ids(db_session):
    db_session.add(RecProcessedEvent(event_id=uuid4(), processed_at=NOW - timedelta(days=20)))
    db_session.add(RecProcessedEvent(event_id=uuid4(), processed_at=NOW))
    db_session.commit()

    assert purge_processed_events(db_session, older_than=NOW - timedelta(days=15)) == 1
    assert db_session.query(RecProcessedEvent).count() == 1
