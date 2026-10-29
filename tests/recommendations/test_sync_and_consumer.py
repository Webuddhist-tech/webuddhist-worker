import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from worker_api.recommendations.consumer import (
    TransientRecommendationEventsError,
    process_recommendation_events_messages,
    run_full_refresh,
)
from worker_api.recommendations.models import RecItem, RecMembership
from worker_api.recommendations.recompute_service import _push
from worker_api.recommendations.schemas import CatalogItem, Membership, ScoredItem
from worker_api.recommendations.sqs_client import parse_recommendation_events_message_body
from worker_api.recommendations.sync_service import replace_catalog, replace_memberships

CONSUMER = "worker_api.recommendations.consumer"
RECOMPUTE = "worker_api.recommendations.recompute_service"


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
    )

    assert (added, removed) == (1, 1)
    remaining = {m.item_id for m in db_session.query(RecMembership).all()}
    assert remaining == {kept, new}


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
        f"{CONSUMER}.full_resync", new_callable=AsyncMock, side_effect=lambda: order.append("sync")
    ), patch(
        f"{CONSUMER}.recompute_all_users",
        new_callable=AsyncMock,
        side_effect=lambda: order.append("score"),
    ), patch(f"{CONSUMER}._purge_processed", return_value=0):
        assert await run_full_refresh() is True
    assert order == ["sync", "score"]


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
