import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from worker_api.recommendations.consumer import (
    TransientRecommendationEventsError,
    process_recommendation_events_messages,
    run_full_refresh,
    run_recommendation_full_refresh_loop,
)
from worker_api.recommendations.models import RecItem, RecMembership, RecMembershipChange
from worker_api.recommendations.recompute_service import _all_user_ids, _push
from worker_api.recommendations.schemas import (
    CatalogItem,
    Membership,
    RecommendationEvent,
    ScoredItem,
)
from worker_api.recommendations.signal_service import apply_events
from worker_api.recommendations.sqs_client import parse_recommendation_events_message_body
from worker_api.recommendations.sync_service import replace_catalog, replace_memberships

CONSUMER = "worker_api.recommendations.consumer"
RECOMPUTE = "worker_api.recommendations.recompute_service"
SNAPSHOT_AT = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def _body(events, event_type="RECOMMENDATION_EVENTS", version=1):
    return json.dumps({"event_type": event_type, "version": version, "events": events})


def _raw_event(**overrides):
    event = {
        "event_id": str(uuid4()),
        "user_id": str(uuid4()),
        "item_type": "SPACE",
        "item_id": str(uuid4()),
        "action": "JOIN",
        "occurred_at": "2026-10-29T00:00:00+00:00",
    }
    event.update(overrides)
    return event


def test_parse_accepts_events_and_skips_bad_ones():
    events = parse_recommendation_events_message_body(
        _body([_raw_event(), {"event_id": "nope"}])
    )
    assert len(events) == 1
    assert events[0].action == "JOIN"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps([1, 2]),
        _body([], event_type="OTHER"),
        _body([], version=2),
        json.dumps({"event_type": "RECOMMENDATION_EVENTS", "version": 1}),
    ],
)
def test_parse_rejects_foreign_messages(raw):
    assert parse_recommendation_events_message_body(raw) is None


def test_replace_catalog_upserts_and_drops_missing(db_session):
    kept, gone = uuid4(), uuid4()
    db_session.add(RecItem(item_type="SPACE", item_id=gone, tag_ids=[]))
    db_session.add(RecItem(item_type="SPACE", item_id=kept, tag_ids=[]))
    db_session.add(RecItem(item_type="PLAN", item_id=uuid4(), tag_ids=[]))
    db_session.commit()

    tradition = uuid4()
    dropped = replace_catalog(
        db_session,
        "SPACE",
        [CatalogItem(item_type="SPACE", item_id=kept, tradition_id=tradition)],
    )

    assert dropped == 1
    spaces = db_session.query(RecItem).filter(RecItem.item_type == "SPACE").all()
    assert [(s.item_id, s.tradition_id) for s in spaces] == [(kept, tradition)]
    # Other types are untouched.
    assert db_session.query(RecItem).filter(RecItem.item_type == "PLAN").count() == 1


def test_replace_memberships_adds_and_removes(db_session):
    user = uuid4()
    stale, kept, new = uuid4(), uuid4(), uuid4()
    for item_id in (stale, kept):
        db_session.add(RecMembership(user_id=user, item_type="SPACE", item_id=item_id))
    db_session.commit()

    added, removed = replace_memberships(
        db_session,
        "SPACE",
        [Membership(user_id=user, item_id=kept), Membership(user_id=user, item_id=new)],
        SNAPSHOT_AT,
    )

    assert (added, removed) == (1, 1)
    remaining = {m.item_id for m in db_session.query(RecMembership).all()}
    assert remaining == {kept, new}


def _membership_event(user, item_id, action, at):
    return RecommendationEvent(
        event_id=uuid4(),
        user_id=user,
        item_type="SPACE",
        item_id=item_id,
        action=action,
        occurred_at=at,
    )


def test_resync_keeps_memberships_changed_by_events_during_the_fetch(db_session):
    user, joined, left = uuid4(), uuid4(), uuid4()
    apply_events(db_session, [_membership_event(user, left, "JOIN", SNAPSHOT_AT - timedelta(days=1))])
    # Both events land while the snapshot, which predates them, is being fetched.
    apply_events(
        db_session,
        [
            _membership_event(user, joined, "JOIN", SNAPSHOT_AT + timedelta(seconds=10)),
            _membership_event(user, left, "LEAVE", SNAPSHOT_AT + timedelta(seconds=20)),
        ],
    )

    added, removed = replace_memberships(
        db_session, "SPACE", [Membership(user_id=user, item_id=left)], SNAPSHOT_AT
    )

    assert (added, removed) == (0, 0)
    assert {m.item_id for m in db_session.query(RecMembership).all()} == {joined}


def test_events_older_than_the_resync_do_not_undo_it(db_session):
    user, space = uuid4(), uuid4()
    replace_memberships(db_session, "SPACE", [Membership(user_id=user, item_id=space)], SNAPSHOT_AT)
    apply_events(
        db_session, [_membership_event(user, space, "LEAVE", SNAPSHOT_AT - timedelta(hours=1))]
    )
    assert db_session.query(RecMembership).count() == 1


def test_users_who_left_everything_are_still_refreshed(db_session):
    by_event, by_resync, space = uuid4(), uuid4(), uuid4()
    db_session.add(RecMembership(user_id=by_resync, item_type="SPACE", item_id=space))
    db_session.commit()
    apply_events(db_session, [_membership_event(by_event, space, "JOIN", SNAPSHOT_AT)])
    apply_events(
        db_session,
        [_membership_event(by_event, space, "LEAVE", SNAPSHOT_AT + timedelta(seconds=1))],
    )
    replace_memberships(db_session, "SPACE", [], SNAPSHOT_AT + timedelta(hours=1))

    assert db_session.query(RecMembership).count() == 0
    assert db_session.query(RecMembershipChange).count() == 2
    with patch(f"{RECOMPUTE}.SessionLocal", return_value=db_session):
        assert set(_all_user_ids()) == {by_event, by_resync}


@pytest.mark.asyncio
async def test_messages_are_applied_deleted_and_users_rescored_once():
    user = str(uuid4())
    messages = [
        {"ReceiptHandle": "r1", "Body": _body([_raw_event(user_id=user)])},
        {"ReceiptHandle": "r2", "Body": _body([_raw_event(user_id=user, action="START", item_type="PLAN")])},
        {"ReceiptHandle": "r3", "Body": "garbage"},
    ]
    with patch(f"{CONSUMER}._apply", return_value={uuid4()}) as mock_apply, patch(
        f"{CONSUMER}.delete_recommendation_events_message"
    ) as mock_delete, patch(
        f"{CONSUMER}.recompute_users", new_callable=AsyncMock, return_value=0
    ) as mock_recompute:
        await process_recommendation_events_messages(messages)

    assert len(mock_apply.call_args.args[0]) == 2
    assert sorted(call.args[0] for call in mock_delete.call_args_list) == ["r1", "r2", "r3"]
    mock_recompute.assert_awaited_once()


@pytest.mark.asyncio
async def test_storage_failure_leaves_messages_for_retry():
    messages = [{"ReceiptHandle": "r1", "Body": _body([_raw_event()])}]
    with patch(f"{CONSUMER}._apply", side_effect=RuntimeError("db down")), patch(
        f"{CONSUMER}.delete_recommendation_events_message"
    ) as mock_delete, pytest.raises(TransientRecommendationEventsError):
        await process_recommendation_events_messages(messages)
    mock_delete.assert_not_called()


@pytest.mark.asyncio
async def test_full_refresh_skips_when_another_worker_holds_the_lock():
    with patch(f"{CONSUMER}._acquire_refresh_lock", return_value=False), patch(
        f"{CONSUMER}.full_resync", new_callable=AsyncMock
    ) as mock_sync:
        assert await run_full_refresh() is False
    mock_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_refresh_resyncs_then_rescores():
    order = []
    with patch(f"{CONSUMER}._acquire_refresh_lock", return_value=True), patch(
        f"{CONSUMER}._release_refresh_lock"
    ), patch(
        f"{CONSUMER}.full_resync", new_callable=AsyncMock, side_effect=lambda: order.append("sync")
    ), patch(
        f"{CONSUMER}.recompute_all_users",
        new_callable=AsyncMock,
        side_effect=lambda: order.append("score"),
    ), patch(f"{CONSUMER}._purge_processed", return_value=0):
        assert await run_full_refresh() is True
    assert order == ["sync", "score"]


@pytest.mark.asyncio
async def test_full_refresh_releases_its_own_lock_even_on_failure():
    redis_client = MagicMock()
    redis_client.set.return_value = True
    with patch(f"{CONSUMER}._get_redis_client", return_value=redis_client), patch(
        f"{CONSUMER}.full_resync", new_callable=AsyncMock, side_effect=RuntimeError("backend down")
    ), pytest.raises(RuntimeError):
        await run_full_refresh()

    token = redis_client.set.call_args.args[1]
    assert redis_client.eval.call_args.args[-1] == token


@pytest.mark.asyncio
async def test_failed_bootstrap_retries_soon_rather_than_after_an_interval():
    stop = asyncio.Event()
    calls = []

    async def refresh():
        calls.append(None)
        if len(calls) == 1:
            raise RuntimeError("backend down")
        stop.set()
        return True

    with patch(f"{CONSUMER}.catalog_is_empty", return_value=True), patch(
        f"{CONSUMER}.run_full_refresh", side_effect=refresh
    ), patch(f"{CONSUMER}._REFRESH_RETRY_SECONDS", 0.01):
        await asyncio.wait_for(run_recommendation_full_refresh_loop(stop), timeout=5)

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_push_counts_failures_but_treats_deleted_users_as_done():
    ok, deleted, broken = uuid4(), uuid4(), uuid4()

    def fail_with(status):
        request = httpx.Request("PUT", "http://backend")
        return httpx.HTTPStatusError("x", request=request, response=httpx.Response(status, request=request))

    async def fake_put(*, user_id, **_):
        if user_id == deleted:
            raise fail_with(404)
        if user_id == broken:
            raise fail_with(500)

    item = ScoredItem(item_type="SPACE", item_id=uuid4(), score=1.0, reason_code="NEW")
    with patch(f"{RECOMPUTE}.put_user_recommendations", side_effect=fake_put) as mock_put:
        failed = await _push({ok: [item], deleted: [], broken: [item]})

    assert failed == 1
    assert mock_put.call_count == 3
    sent = {call.kwargs["user_id"]: call.kwargs for call in mock_put.call_args_list}
    assert sent[ok]["model"] == "rules-v1"
    assert sent[ok]["items"] == [item]
    assert isinstance(sent[ok]["computed_at"], datetime)
