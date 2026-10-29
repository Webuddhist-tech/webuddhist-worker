from datetime import datetime, timedelta, timezone
from uuid import uuid4

from worker_api.recommendations.models import (
    RecDismissal,
    RecExposure,
    RecItem,
    RecMembership,
)
from worker_api.recommendations.scoring import (
    REASON_CO_ENROLLED,
    REASON_CO_MEMBERS,
    REASON_FROM_JOINED_SPACE,
    REASON_NEW,
    REASON_TAG_AFFINITY,
    REASON_TRADITION_MATCH,
    build_context,
    score_user,
)

NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=365)


def _space(db, tradition_id=None, published_at=OLD):
    item = RecItem(item_type="SPACE", item_id=uuid4(), tradition_id=tradition_id, tag_ids=[], published_at=published_at)
    db.add(item)
    return item.item_id


def _plan(db, group_id=None, tags=(), tradition_id=None, published_at=OLD):
    item = RecItem(
        item_type="PLAN",
        item_id=uuid4(),
        group_id=group_id,
        tradition_id=tradition_id,
        tag_ids=[str(tag) for tag in tags],
        published_at=published_at,
    )
    db.add(item)
    return item.item_id


def _member(db, user_id, item_type, item_id, joined_at=OLD):
    db.add(RecMembership(user_id=user_id, item_type=item_type, item_id=item_id, joined_at=joined_at))


def _by_id(results):
    return {item.item_id: item for item in results}


def test_co_members_suggest_spaces_my_spaces_share(db_session):
    me, other = uuid4(), uuid4()
    mine, shared = _space(db_session), _space(db_session)
    _member(db_session, me, "SPACE", mine)
    _member(db_session, other, "SPACE", mine)
    _member(db_session, other, "SPACE", shared)
    db_session.commit()

    results = _by_id(score_user(db_session, build_context(db_session, NOW), me))

    assert mine not in results
    assert results[shared].reason_code == REASON_CO_MEMBERS
    assert results[shared].reason_args == {"count": 1}


def test_tradition_and_joined_space_signals(db_session):
    me = uuid4()
    tradition = uuid4()
    my_space = _space(db_session)
    same_tradition_space = _space(db_session, tradition_id=tradition)
    plan_in_my_space = _plan(db_session, group_id=my_space)
    _member(db_session, me, "SPACE", my_space)
    _member(db_session, me, "TRADITION", tradition)
    db_session.commit()

    results = _by_id(score_user(db_session, build_context(db_session, NOW), me))

    assert results[same_tradition_space].reason_code == REASON_TRADITION_MATCH
    assert results[same_tradition_space].reason_args == {"tradition_id": str(tradition)}
    assert results[plan_in_my_space].reason_code == REASON_FROM_JOINED_SPACE


def test_tag_affinity_and_co_enrolment_for_plans(db_session):
    me, other = uuid4(), uuid4()
    tag = uuid4()
    my_plan = _plan(db_session, tags=[tag])
    tagged = _plan(db_session, tags=[tag])
    co_enrolled = _plan(db_session)
    _member(db_session, me, "PLAN", my_plan)
    _member(db_session, other, "PLAN", my_plan)
    _member(db_session, other, "PLAN", co_enrolled)
    db_session.commit()

    results = _by_id(score_user(db_session, build_context(db_session, NOW), me))

    assert my_plan not in results
    assert results[tagged].reason_code == REASON_TAG_AFFINITY
    assert results[co_enrolled].reason_code == REASON_CO_ENROLLED


def test_dismissed_and_unknown_items_are_never_suggested(db_session):
    me, other = uuid4(), uuid4()
    mine, dismissed = _space(db_session), _space(db_session)
    unpublished = uuid4()  # in memberships but not in the catalog
    for space in (mine, dismissed, unpublished):
        _member(db_session, other, "SPACE", space)
    _member(db_session, me, "SPACE", mine)
    db_session.add(RecDismissal(user_id=me, item_type="SPACE", item_id=dismissed))
    db_session.commit()

    results = _by_id(score_user(db_session, build_context(db_session, NOW), me))

    assert dismissed not in results
    assert unpublished not in results


def test_ignored_suggestions_sink(db_session):
    me = uuid4()
    tradition = uuid4()
    seen, fresh = _space(db_session, tradition_id=tradition), _space(db_session, tradition_id=tradition)
    _member(db_session, me, "TRADITION", tradition)
    db_session.add(RecExposure(user_id=me, item_type="SPACE", item_id=seen, impressions=10, clicks=0))
    db_session.commit()

    results = _by_id(score_user(db_session, build_context(db_session, NOW), me))

    # 7 ignored impressions past the free 3 outweigh the tradition match.
    assert fresh in results
    assert seen not in results


def test_new_items_get_a_freshness_boost(db_session):
    me = uuid4()
    fresh = _space(db_session, published_at=NOW - timedelta(days=1))
    _space(db_session, published_at=OLD)
    db_session.commit()

    results = score_user(db_session, build_context(db_session, NOW), me)

    assert [item.item_id for item in results] == [fresh]
    assert results[0].reason_code == REASON_NEW


def test_results_are_capped_per_type(db_session, monkeypatch):
    monkeypatch.setenv("RECOMMENDATION_MAX_PER_TYPE", "2")
    me = uuid4()
    for _ in range(5):
        _space(db_session, published_at=NOW - timedelta(days=1))
    db_session.commit()

    results = score_user(db_session, build_context(db_session, NOW), me)
    assert len(results) == 2
