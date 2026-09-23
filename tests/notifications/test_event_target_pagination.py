"""Pagination and error mapping for the three event-target fetch helpers.

The consumer tests all patch these helpers out, so without this file the
paging loop, the 404 path and the transient-error mapping never run: a
backend that answered in pages, or failed, would be discovered in
production rather than here.
"""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from worker_api.notifications.schemas import (
    EventAnnouncementTargetsResponse,
    EventNotificationRecipient,
    EventNotificationTargetsResponse,
    EventPushDeviceTarget,
    EventReminderTargetsResponse,
)
from worker_api.notifications.services.event_notification_consumer import (
    TransientEventNotificationError,
    _fetch_all_announcement_targets,
    _fetch_all_reminder_targets,
    _fetch_all_targets,
)

CONSUMER = "worker_api.notifications.services.event_notification_consumer"

PAGE_SIZE = 2


def _recipient():
    return EventNotificationRecipient(
        user_id=uuid4(),
        push_devices=[
            EventPushDeviceTarget(id=uuid4(), token=f"token-{uuid4()}", platform="ios")
        ],
    )


def _patch_page_size():
    return patch(f"{CONSUMER}.get_int", return_value=PAGE_SIZE)


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://backend.test/internal")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def _created_page(event_id, *, recipients, skip, has_more):
    return EventNotificationTargetsResponse(
        event_id=event_id,
        group_id=uuid4(),
        author_id=uuid4(),
        title="Sangha",
        body="Full Moon Meditation",
        recipients=recipients,
        skip=skip,
        limit=PAGE_SIZE,
        total=4,
        has_more=has_more,
    )


def _reminder_page(event_id, *, recipients, skip, has_more):
    return EventReminderTargetsResponse(
        event_id=event_id,
        reminder_type="T_MINUS_10",
        title="Sangha",
        body="Starting in 10 minutes",
        recipients=recipients,
        skip=skip,
        limit=PAGE_SIZE,
        total=4,
        has_more=has_more,
    )


def _announcement_page(event_id, *, recipients, skip, has_more):
    return EventAnnouncementTargetsResponse(
        event_id=event_id,
        audience="participants",
        recipients=recipients,
        skip=skip,
        limit=PAGE_SIZE,
        total=4,
        has_more=has_more,
    )


class TestFetchAllAnnouncementTargets:
    @pytest.mark.asyncio
    async def test_walks_every_page_and_flattens_recipients(self):
        event_id = uuid4()
        first = _announcement_page(
            event_id, recipients=[_recipient(), _recipient()], skip=0, has_more=True
        )
        second = _announcement_page(
            event_id, recipients=[_recipient()], skip=2, has_more=False
        )
        fetch = AsyncMock(side_effect=[first, second])

        with patch(f"{CONSUMER}.fetch_event_announcement_targets", fetch), _patch_page_size():
            targets = await _fetch_all_announcement_targets(event_id, "participants")

        assert len(targets.recipients) == 3
        # The merged result stands in for the whole set, so nothing downstream
        # should think there is another page waiting.
        assert targets.has_more is False
        assert [call.kwargs["skip"] for call in fetch.await_args_list] == [0, 2]
        assert {call.kwargs["limit"] for call in fetch.await_args_list} == {PAGE_SIZE}
        assert {call.kwargs["audience"] for call in fetch.await_args_list} == {
            "participants"
        }

    @pytest.mark.asyncio
    async def test_stops_after_a_single_complete_page(self):
        event_id = uuid4()
        only = _announcement_page(
            event_id, recipients=[_recipient()], skip=0, has_more=False
        )
        fetch = AsyncMock(return_value=only)

        with patch(f"{CONSUMER}.fetch_event_announcement_targets", fetch), _patch_page_size():
            targets = await _fetch_all_announcement_targets(event_id, "group")

        assert len(targets.recipients) == 1
        fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_deleted_event_becomes_a_404(self):
        """Distinct from a transient failure: the caller deletes the SQS
        message on this one instead of leaving it to retry forever."""
        fetch = AsyncMock(side_effect=_http_status_error(404))

        with patch(f"{CONSUMER}.fetch_event_announcement_targets", fetch), _patch_page_size():
            with pytest.raises(HTTPException) as exc_info:
                await _fetch_all_announcement_targets(uuid4(), "participants")

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_server_error_is_transient(self):
        fetch = AsyncMock(side_effect=_http_status_error(503))

        with patch(f"{CONSUMER}.fetch_event_announcement_targets", fetch), _patch_page_size():
            with pytest.raises(TransientEventNotificationError):
                await _fetch_all_announcement_targets(uuid4(), "participants")

    @pytest.mark.asyncio
    async def test_a_connection_failure_is_transient(self):
        fetch = AsyncMock(side_effect=httpx.ConnectError("backend unreachable"))

        with patch(f"{CONSUMER}.fetch_event_announcement_targets", fetch), _patch_page_size():
            with pytest.raises(TransientEventNotificationError):
                await _fetch_all_announcement_targets(uuid4(), "participants")


class TestFetchAllReminderTargets:
    @pytest.mark.asyncio
    async def test_walks_every_page_and_carries_the_schedule(self):
        event_id = uuid4()
        fire_at = "2026-10-01T08:50:00+00:00"
        first = _reminder_page(
            event_id, recipients=[_recipient(), _recipient()], skip=0, has_more=True
        )
        second = _reminder_page(
            event_id, recipients=[_recipient()], skip=2, has_more=False
        )
        fetch = AsyncMock(side_effect=[first, second])

        with patch(f"{CONSUMER}.fetch_event_reminder_targets", fetch), _patch_page_size():
            targets = await _fetch_all_reminder_targets(event_id, "T_MINUS_10", fire_at)

        assert len(targets.recipients) == 3
        assert targets.has_more is False
        # fire_at has to ride along on every page, not just the first: it is
        # what lets the backend reject a message superseded by a reschedule.
        assert {call.kwargs["fire_at"] for call in fetch.await_args_list} == {fire_at}
        assert [call.kwargs["skip"] for call in fetch.await_args_list] == [0, 2]

    @pytest.mark.asyncio
    async def test_a_deleted_event_becomes_a_404(self):
        fetch = AsyncMock(side_effect=_http_status_error(404))

        with patch(f"{CONSUMER}.fetch_event_reminder_targets", fetch), _patch_page_size():
            with pytest.raises(HTTPException) as exc_info:
                await _fetch_all_reminder_targets(uuid4(), "T_ZERO", None)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_server_error_is_transient(self):
        fetch = AsyncMock(side_effect=_http_status_error(500))

        with patch(f"{CONSUMER}.fetch_event_reminder_targets", fetch), _patch_page_size():
            with pytest.raises(TransientEventNotificationError):
                await _fetch_all_reminder_targets(uuid4(), "T_ZERO", None)

    @pytest.mark.asyncio
    async def test_a_connection_failure_is_transient(self):
        fetch = AsyncMock(side_effect=httpx.ReadTimeout("slow backend"))

        with patch(f"{CONSUMER}.fetch_event_reminder_targets", fetch), _patch_page_size():
            with pytest.raises(TransientEventNotificationError):
                await _fetch_all_reminder_targets(uuid4(), "T_ZERO", None)


class TestFetchAllCreatedTargets:
    @pytest.mark.asyncio
    async def test_walks_every_page_and_flattens_recipients(self):
        event_id = uuid4()
        first = _created_page(
            event_id, recipients=[_recipient(), _recipient()], skip=0, has_more=True
        )
        second = _created_page(
            event_id, recipients=[_recipient()], skip=2, has_more=False
        )
        fetch = AsyncMock(side_effect=[first, second])

        with patch(f"{CONSUMER}.fetch_event_notification_targets", fetch), _patch_page_size():
            targets = await _fetch_all_targets(event_id)

        assert len(targets.recipients) == 3
        assert targets.has_more is False
        # The copy comes from the first page and must survive the merge.
        assert targets.title == "Sangha"
        assert [call.kwargs["skip"] for call in fetch.await_args_list] == [0, 2]

    @pytest.mark.asyncio
    async def test_a_deleted_event_becomes_a_404(self):
        fetch = AsyncMock(side_effect=_http_status_error(404))

        with patch(f"{CONSUMER}.fetch_event_notification_targets", fetch), _patch_page_size():
            with pytest.raises(HTTPException) as exc_info:
                await _fetch_all_targets(uuid4())

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_server_error_is_transient(self):
        fetch = AsyncMock(side_effect=_http_status_error(502))

        with patch(f"{CONSUMER}.fetch_event_notification_targets", fetch), _patch_page_size():
            with pytest.raises(TransientEventNotificationError):
                await _fetch_all_targets(uuid4())

    @pytest.mark.asyncio
    async def test_a_connection_failure_is_transient(self):
        fetch = AsyncMock(side_effect=httpx.ConnectError("backend unreachable"))

        with patch(f"{CONSUMER}.fetch_event_notification_targets", fetch), _patch_page_size():
            with pytest.raises(TransientEventNotificationError):
                await _fetch_all_targets(uuid4())


class TestPageSizeFloor:
    @pytest.mark.asyncio
    async def test_a_zero_page_size_still_requests_one_row(self):
        """A misconfigured page size of 0 would otherwise ask the backend for
        nothing and loop on an empty page forever."""
        event_id = uuid4()
        only = _announcement_page(
            event_id, recipients=[_recipient()], skip=0, has_more=False
        )
        fetch = AsyncMock(return_value=only)

        with patch(f"{CONSUMER}.fetch_event_announcement_targets", fetch), patch(
            f"{CONSUMER}.get_int", return_value=0
        ):
            await _fetch_all_announcement_targets(event_id, "participants")

        assert fetch.await_args.kwargs["limit"] == 1
