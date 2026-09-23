"""Delivery of organizer-written notifications about an event."""
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from worker_api.notifications.event_sqs_client import (
    parse_event_notification_message_body,
)
from worker_api.notifications.schemas import (
    EventAnnouncementTargetsResponse,
    EventNotificationRecipient,
    EventPushDeviceTarget,
)
from worker_api.notifications.services.event_notification_consumer import (
    _idempotency_key,
    process_event_notification_message,
)

CONSUMER = "worker_api.notifications.services.event_notification_consumer"


def _announcement_body(event_id, **overrides):
    body = {
        "event_type": "EVENT_ANNOUNCEMENT",
        "version": 1,
        "event_id": str(event_id),
        "announcement_id": str(uuid4()),
        "audience": "participants",
        "title": "Change of venue",
        "body": "We are in the main hall today.",
    }
    body.update(overrides)
    return json.dumps(body)


def _announcement_targets(*, event_id, devices, audience="participants"):
    return EventAnnouncementTargetsResponse(
        event_id=event_id,
        audience=audience,
        recipients=[
            EventNotificationRecipient(user_id=uuid4(), push_devices=devices)
        ],
        skip=0,
        limit=100,
        total=1,
        has_more=False,
    )


class TestParsing:
    def test_accepts_a_complete_announcement(self):
        event_id = uuid4()
        assert parse_event_notification_message_body(
            _announcement_body(event_id)
        ) is not None

    @pytest.mark.parametrize("field", ["announcement_id", "title", "body"])
    def test_rejects_a_message_missing_what_it_needs_to_render(self, field):
        """The copy exists only in the message, so one that lost it can never
        be delivered - dropping it here beats failing once per device."""
        assert (
            parse_event_notification_message_body(
                _announcement_body(uuid4(), **{field: ""})
            )
            is None
        )

    def test_rejects_an_unknown_audience(self):
        assert (
            parse_event_notification_message_body(
                _announcement_body(uuid4(), audience="everyone")
            )
            is None
        )


class TestIdempotencyKey:
    @patch(f"{CONSUMER}.get", return_value="worker:event-notifications:sent:")
    def test_two_sends_of_the_same_words_are_two_announcements(self, _get):
        """Deduplication is by announcement id, not by text: an organizer who
        sends the same reminder twice on purpose means it twice."""
        event_id = uuid4()
        device_id = uuid4()
        first = _idempotency_key(
            event_id=event_id, push_device_id=device_id, announcement_id="a1"
        )
        second = _idempotency_key(
            event_id=event_id, push_device_id=device_id, announcement_id="a2"
        )

        assert first != second
        assert first == (
            f"worker:event-notifications:sent:{event_id}:ANNOUNCEMENT:a1:{device_id}"
        )

    @patch(f"{CONSUMER}.get", return_value="worker:event-notifications:sent:")
    def test_does_not_collide_with_a_reminder_for_the_same_event(self, _get):
        event_id = uuid4()
        device_id = uuid4()
        announcement = _idempotency_key(
            event_id=event_id, push_device_id=device_id, announcement_id="a1"
        )
        reminder = _idempotency_key(
            event_id=event_id,
            push_device_id=device_id,
            reminder_type="T_MINUS_10",
            fire_at="2026-10-01T08:50:00+00:00",
        )

        assert announcement != reminder


class TestProcessAnnouncement:
    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_event_notification_message")
    @patch(
        f"{CONSUMER}.send_event_announcement_push_notification",
        new_callable=AsyncMock,
    )
    @patch(f"{CONSUMER}._fetch_all_announcement_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.is_push_configured", return_value=True)
    @patch(f"{CONSUMER}._already_sent", return_value=False)
    @patch(f"{CONSUMER}._mark_sent")
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_sends_the_organizers_own_copy(
        self,
        _get_bool,
        _get_int,
        mock_mark,
        _already,
        _configured,
        mock_fetch,
        mock_send,
        mock_delete,
    ):
        event_id = uuid4()
        device = EventPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _announcement_targets(
            event_id=event_id, devices=[device]
        )
        raw = _announcement_body(event_id)
        announcement_id = json.loads(raw)["announcement_id"]

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": raw}
        )

        send_kwargs = mock_send.await_args.kwargs
        assert send_kwargs["title"] == "Change of venue"
        assert send_kwargs["body"] == "We are in the main hall today."
        assert send_kwargs["announcement_id"] == announcement_id
        mock_mark.assert_called_once_with(
            event_id=event_id,
            push_device_id=device.id,
            announcement_id=announcement_id,
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_event_notification_message")
    @patch(
        f"{CONSUMER}.send_event_announcement_push_notification",
        new_callable=AsyncMock,
    )
    @patch(f"{CONSUMER}._fetch_all_announcement_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_no_recipients_drops_the_message(
        self, _get_bool, mock_fetch, mock_send, mock_delete
    ):
        """Also what a suppressed announcement looks like: the backend returns
        nobody once the event's notifications switch goes off."""
        event_id = uuid4()
        mock_fetch.return_value = EventAnnouncementTargetsResponse(
            event_id=event_id,
            audience="participants",
            recipients=[],
            skip=0,
            limit=100,
            total=0,
            has_more=False,
        )

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _announcement_body(event_id)}
        )

        mock_send.assert_not_awaited()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_event_notification_message")
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_a_malformed_announcement_is_deleted_not_retried(
        self, _get_bool, mock_delete
    ):
        await process_event_notification_message(
            {
                "ReceiptHandle": "r1",
                "Body": _announcement_body(uuid4(), title=""),
            }
        )

        mock_delete.assert_called_once_with("r1")
