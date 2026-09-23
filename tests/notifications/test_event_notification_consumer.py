"""Tests for event notification SQS consumer."""
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from worker_api.notifications.schemas import (
    EventNotificationRecipient,
    EventNotificationTargetsResponse,
    EventPushDeviceTarget,
    EventReminderTargetsResponse,
)
from worker_api.notifications.services.event_notification_consumer import (
    TransientEventNotificationError,
    _idempotency_key,
    process_event_notification_message,
)
from worker_api.notifications.services.push.fcm_client import PermanentPushTokenError


def _targets(*, event_id, devices):
    return EventNotificationTargetsResponse(
        event_id=event_id,
        group_id=uuid4(),
        author_id=uuid4(),
        title="Sangha",
        body="Full Moon Meditation",
        recipients=[
            EventNotificationRecipient(
                user_id=uuid4(),
                push_devices=devices,
            )
        ],
        skip=0,
        limit=100,
        total=1,
        has_more=False,
    )


def _reminder_targets(*, event_id, reminder_type, devices):
    return EventReminderTargetsResponse(
        event_id=event_id,
        reminder_type=reminder_type,
        title="Sangha",
        body="Starting in 10 minutes" if reminder_type == "T_MINUS_10" else "Starting now",
        recipients=[
            EventNotificationRecipient(
                user_id=uuid4(),
                push_devices=devices,
            )
        ],
        skip=0,
        limit=100,
        total=1,
        has_more=False,
    )


def _body(event_id):
    return json.dumps(
        {
            "event_type": "EVENT_CREATED",
            "version": 1,
            "event_id": str(event_id),
        }
    )


def _reminder_body(event_id, reminder_type, fire_at=None):
    body = {
        "event_type": "EVENT_REMINDER",
        "version": 1,
        "event_id": str(event_id),
        "reminder_type": reminder_type,
    }
    if fire_at is not None:
        body["fire_at"] = fire_at
    return json.dumps(body)


class TestIdempotencyKey:
    @patch(
        "worker_api.notifications.services.event_notification_consumer.get",
        return_value="worker:event-notifications:sent:",
    )
    def test_created_key_has_no_reminder_segment(self, _get):
        event_id = uuid4()
        device_id = uuid4()
        key = _idempotency_key(event_id=event_id, push_device_id=device_id)
        assert key == f"worker:event-notifications:sent:{event_id}:{device_id}"

    @patch(
        "worker_api.notifications.services.event_notification_consumer.get",
        return_value="worker:event-notifications:sent:",
    )
    def test_reminder_keys_differ_by_reminder_type(self, _get):
        event_id = uuid4()
        device_id = uuid4()
        t_minus_10_key = _idempotency_key(
            event_id=event_id, push_device_id=device_id, reminder_type="T_MINUS_10"
        )
        t_zero_key = _idempotency_key(
            event_id=event_id, push_device_id=device_id, reminder_type="T_ZERO"
        )
        created_key = _idempotency_key(event_id=event_id, push_device_id=device_id)

        assert t_minus_10_key != t_zero_key
        assert t_minus_10_key != created_key
        assert t_zero_key != created_key
        assert t_minus_10_key == f"worker:event-notifications:sent:{event_id}:T_MINUS_10:{device_id}"
        assert t_zero_key == f"worker:event-notifications:sent:{event_id}:T_ZERO:{device_id}"

    @patch(
        "worker_api.notifications.services.event_notification_consumer.get",
        return_value="worker:event-notifications:sent:",
    )
    def test_reminder_keys_differ_by_day(self, _get):
        """Regression guard: a multi-day or recurring event sends the same
        (event, type) pair on consecutive days, exactly 24h apart - the same
        as EVENT_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS. Without the schedule in
        the key, day two races the TTL of day one and is silently dropped."""
        event_id = uuid4()
        device_id = uuid4()
        day_one = "2026-10-01T08:50:00+00:00"
        day_two = "2026-10-02T08:50:00+00:00"

        first = _idempotency_key(
            event_id=event_id,
            push_device_id=device_id,
            reminder_type="T_MINUS_10",
            fire_at=day_one,
        )
        second = _idempotency_key(
            event_id=event_id,
            push_device_id=device_id,
            reminder_type="T_MINUS_10",
            fire_at=day_two,
        )

        assert first != second
        assert first == (
            f"worker:event-notifications:sent:{event_id}:T_MINUS_10:{day_one}:{device_id}"
        )

    @patch(
        "worker_api.notifications.services.event_notification_consumer.get",
        return_value="worker:event-notifications:sent:",
    )
    def test_message_without_a_schedule_keeps_the_old_key_shape(self, _get):
        """Only possible for a message queued before fire_at existed; it must
        still resolve to the key its earlier delivery would have used."""
        event_id = uuid4()
        device_id = uuid4()

        key = _idempotency_key(
            event_id=event_id, push_device_id=device_id, reminder_type="T_ZERO", fire_at=None
        )

        assert key == f"worker:event-notifications:sent:{event_id}:T_ZERO:{device_id}"


class TestProcessEventNotificationMessage:
    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    async def test_deletes_malformed_message(self, mock_delete):
        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": "not-json"}
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    @patch(
        "worker_api.notifications.services.event_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch("worker_api.notifications.services.event_notification_consumer.get_bool", return_value=True)
    async def test_deletes_when_event_not_found(self, _get_bool, mock_fetch, mock_delete):
        from fastapi import HTTPException

        mock_fetch.side_effect = HTTPException(status_code=404, detail="not found")
        event_id = uuid4()
        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _body(event_id)}
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    @patch(
        "worker_api.notifications.services.event_notification_consumer.send_event_push_notification",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._already_sent",
        return_value=False,
    )
    @patch("worker_api.notifications.services.event_notification_consumer._mark_sent")
    @patch("worker_api.notifications.services.event_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.event_notification_consumer.get_bool", return_value=True)
    async def test_sends_and_deletes_on_success(
        self, _get_bool, _get_int, mock_mark, _already, _configured, mock_fetch, mock_send, mock_delete,
    ):
        event_id = uuid4()
        device = EventPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(event_id=event_id, devices=[device])

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _body(event_id)}
        )

        mock_send.assert_awaited_once()
        mock_mark.assert_called_once()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    @patch(
        "worker_api.notifications.services.event_notification_consumer.deactivate_push_device",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer.send_event_push_notification",
        new_callable=AsyncMock,
        side_effect=PermanentPushTokenError("gone"),
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._already_sent",
        return_value=False,
    )
    @patch("worker_api.notifications.services.event_notification_consumer._mark_sent")
    @patch("worker_api.notifications.services.event_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.event_notification_consumer.get_bool", return_value=True)
    async def test_permanent_token_deactivates_and_deletes(
        self, _get_bool, _get_int, mock_mark, _already, _configured, mock_fetch, mock_send, mock_deactivate, mock_delete,
    ):
        event_id = uuid4()
        device = EventPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(event_id=event_id, devices=[device])

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _body(event_id)}
        )

        mock_deactivate.assert_awaited_once_with(push_device_id=device.id)
        mock_mark.assert_called_once()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    @patch(
        "worker_api.notifications.services.event_notification_consumer.send_event_push_notification",
        new_callable=AsyncMock,
        side_effect=RuntimeError("temporary"),
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._already_sent",
        return_value=False,
    )
    @patch("worker_api.notifications.services.event_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.event_notification_consumer.get_bool", return_value=True)
    async def test_transient_failure_leaves_message(
        self, _get_bool, _get_int, _already, _configured, mock_fetch, mock_send, mock_delete,
    ):
        event_id = uuid4()
        device = EventPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(event_id=event_id, devices=[device])

        with pytest.raises(TransientEventNotificationError):
            await process_event_notification_message(
                {"ReceiptHandle": "r1", "Body": _body(event_id)}
            )

        mock_delete.assert_not_called()

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    @patch(
        "worker_api.notifications.services.event_notification_consumer.send_event_push_notification",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._already_sent",
        return_value=True,
    )
    @patch("worker_api.notifications.services.event_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.event_notification_consumer.get_bool", return_value=True)
    async def test_skips_already_sent_devices(
        self, _get_bool, _get_int, _already, _configured, mock_fetch, mock_send, mock_delete,
    ):
        event_id = uuid4()
        device = EventPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(event_id=event_id, devices=[device])

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _body(event_id)}
        )

        mock_send.assert_not_called()
        mock_delete.assert_called_once_with("r1")


class TestProcessEventReminderMessage:
    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    @patch(
        "worker_api.notifications.services.event_notification_consumer._fetch_all_reminder_targets",
        new_callable=AsyncMock,
    )
    @patch("worker_api.notifications.services.event_notification_consumer.get_bool", return_value=True)
    async def test_deletes_when_event_not_found(self, _get_bool, mock_fetch, mock_delete):
        from fastapi import HTTPException

        mock_fetch.side_effect = HTTPException(status_code=404, detail="not found")
        event_id = uuid4()
        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _reminder_body(event_id, "T_MINUS_10")}
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    @patch(
        "worker_api.notifications.services.event_notification_consumer._fetch_all_reminder_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer.is_push_configured",
        return_value=False,
    )
    @patch("worker_api.notifications.services.event_notification_consumer.get_bool", return_value=True)
    async def test_forwards_fire_at_from_the_message_to_target_resolution(
        self, _get_bool, _configured, mock_fetch, _mock_delete,
    ):
        """Regression guard: fire_at is what lets the backend tell apart a
        stale message that outlived a cancel/reschedule from a fresh
        dispatch of the same (event_id, reminder_type) row - dropping it
        here would silently re-open that race."""
        event_id = uuid4()
        device = EventPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _reminder_targets(
            event_id=event_id, reminder_type="T_MINUS_10", devices=[device]
        )

        await process_event_notification_message(
            {
                "ReceiptHandle": "r1",
                "Body": _reminder_body(event_id, "T_MINUS_10", fire_at="2026-06-15T05:50:00+00:00"),
            }
        )

        mock_fetch.assert_awaited_once_with(event_id, "T_MINUS_10", "2026-06-15T05:50:00+00:00")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    @patch(
        "worker_api.notifications.services.event_notification_consumer._fetch_all_reminder_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer.is_push_configured",
        return_value=False,
    )
    @patch("worker_api.notifications.services.event_notification_consumer.get_bool", return_value=True)
    async def test_missing_fire_at_forwards_none(self, _get_bool, _configured, mock_fetch, _mock_delete):
        """An older-format message (published before this field existed)
        must still process, just without the extra staleness check."""
        event_id = uuid4()
        device = EventPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _reminder_targets(
            event_id=event_id, reminder_type="T_ZERO", devices=[device]
        )

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _reminder_body(event_id, "T_ZERO")}
        )

        mock_fetch.assert_awaited_once_with(event_id, "T_ZERO", None)

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    @patch(
        "worker_api.notifications.services.event_notification_consumer.send_event_reminder_push_notification",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._fetch_all_reminder_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._already_sent",
        return_value=False,
    )
    @patch("worker_api.notifications.services.event_notification_consumer._mark_sent")
    @patch("worker_api.notifications.services.event_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.event_notification_consumer.get_bool", return_value=True)
    async def test_sends_and_deletes_on_success(
        self, _get_bool, _get_int, mock_mark, mock_already, _configured, mock_fetch, mock_send, mock_delete,
    ):
        event_id = uuid4()
        device = EventPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _reminder_targets(
            event_id=event_id, reminder_type="T_MINUS_10", devices=[device]
        )

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _reminder_body(event_id, "T_MINUS_10")}
        )

        mock_send.assert_awaited_once()
        send_kwargs = mock_send.await_args.kwargs
        assert send_kwargs["reminder_type"] == "T_MINUS_10"
        assert send_kwargs["body"] == "Starting in 10 minutes"

        mock_already.assert_called_once_with(
            event_id=event_id,
            push_device_id=device.id,
            reminder_type="T_MINUS_10",
            fire_at=None,
        )
        mock_mark.assert_called_once_with(
            event_id=event_id,
            push_device_id=device.id,
            reminder_type="T_MINUS_10",
            fire_at=None,
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    @patch(
        "worker_api.notifications.services.event_notification_consumer.deactivate_push_device",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer.send_event_reminder_push_notification",
        new_callable=AsyncMock,
        side_effect=PermanentPushTokenError("gone"),
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._fetch_all_reminder_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._already_sent",
        return_value=False,
    )
    @patch("worker_api.notifications.services.event_notification_consumer._mark_sent")
    @patch("worker_api.notifications.services.event_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.event_notification_consumer.get_bool", return_value=True)
    async def test_permanent_token_deactivates_and_deletes(
        self, _get_bool, _get_int, mock_mark, _already, _configured, mock_fetch, mock_send, mock_deactivate, mock_delete,
    ):
        event_id = uuid4()
        device = EventPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _reminder_targets(
            event_id=event_id, reminder_type="T_ZERO", devices=[device]
        )

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _reminder_body(event_id, "T_ZERO")}
        )

        mock_deactivate.assert_awaited_once_with(push_device_id=device.id)
        mock_mark.assert_called_once_with(
            event_id=event_id, push_device_id=device.id, reminder_type="T_ZERO", fire_at=None
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    @patch(
        "worker_api.notifications.services.event_notification_consumer.send_event_reminder_push_notification",
        new_callable=AsyncMock,
        side_effect=RuntimeError("temporary"),
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._fetch_all_reminder_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._already_sent",
        return_value=False,
    )
    @patch("worker_api.notifications.services.event_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.event_notification_consumer.get_bool", return_value=True)
    async def test_transient_failure_leaves_message(
        self, _get_bool, _get_int, _already, _configured, mock_fetch, mock_send, mock_delete,
    ):
        event_id = uuid4()
        device = EventPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _reminder_targets(
            event_id=event_id, reminder_type="T_MINUS_10", devices=[device]
        )

        with pytest.raises(TransientEventNotificationError):
            await process_event_notification_message(
                {"ReceiptHandle": "r1", "Body": _reminder_body(event_id, "T_MINUS_10")}
            )

        mock_delete.assert_not_called()

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    @patch(
        "worker_api.notifications.services.event_notification_consumer.send_event_reminder_push_notification",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._fetch_all_reminder_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.event_notification_consumer._already_sent",
        return_value=True,
    )
    @patch("worker_api.notifications.services.event_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.event_notification_consumer.get_bool", return_value=True)
    async def test_skips_already_sent_devices(
        self, _get_bool, _get_int, _already, _configured, mock_fetch, mock_send, mock_delete,
    ):
        event_id = uuid4()
        device = EventPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _reminder_targets(
            event_id=event_id, reminder_type="T_ZERO", devices=[device]
        )

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _reminder_body(event_id, "T_ZERO")}
        )

        mock_send.assert_not_called()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.event_notification_consumer.delete_event_notification_message")
    async def test_rejects_message_with_invalid_reminder_type(self, mock_delete):
        event_id = uuid4()
        body = json.dumps(
            {
                "event_type": "EVENT_REMINDER",
                "version": 1,
                "event_id": str(event_id),
                "reminder_type": "T_MINUS_60",
            }
        )
        await process_event_notification_message({"ReceiptHandle": "r1", "Body": body})
        mock_delete.assert_called_once_with("r1")
