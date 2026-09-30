"""Tests for the prayer notification SQS client and consumer."""
import asyncio
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from worker_api.notifications.prayer_sqs_client import (
    PRAYER_RECEIVED_EVENT,
    parse_prayer_notification_message_body,
)
from worker_api.notifications.schemas import (
    ChatNotificationRecipient,
    ChatPushDeviceTarget,
    PrayerNotificationTargetsResponse,
)
from worker_api.notifications.services import prayer_notification_consumer as consumer
from worker_api.notifications.services import prayer_notification_service as service
from worker_api.notifications.services.chat_notification_consumer import (
    TransientChatNotificationError,
    process_chat_notification_message,
)

_MODULE = "worker_api.notifications.services.prayer_notification_consumer"
_SERVICE = "worker_api.notifications.services.prayer_notification_service"


def _prayer_body(prayer_id):
    return json.dumps(
        {"event_type": PRAYER_RECEIVED_EVENT, "version": 1, "prayer_id": str(prayer_id)}
    )


def _targets(*, prayer_id, devices):
    return PrayerNotificationTargetsResponse(
        prayer_id=prayer_id,
        message_id=uuid4(),
        room_id=uuid4(),
        chat_kind="GROUP",
        group_id=uuid4(),
        event_id=None,
        requester_id=uuid4(),
        prayer_count=3,
        title="Tenzin prayed for you",
        body="🙏",
        recipients=[ChatNotificationRecipient(user_id=uuid4(), push_devices=devices)],
        skip=0,
        limit=100,
        total=1,
        has_more=False,
    )


class TestParsePrayerNotificationMessageBody:
    def test_valid_event(self):
        prayer_id = uuid4()
        body = parse_prayer_notification_message_body(_prayer_body(prayer_id))
        assert body["prayer_id"] == str(prayer_id)

    def test_accepts_prayer_request_chat_event(self):
        message_id = str(uuid4())
        raw = json.dumps(
            {"event_type": "CHAT_MESSAGE_CREATED", "version": 1, "message_id": message_id}
        )
        assert parse_prayer_notification_message_body(raw)["message_id"] == message_id

    def test_rejects_prayer_request_without_message_id(self):
        raw = json.dumps({"event_type": "CHAT_MESSAGE_CREATED", "version": 1})
        assert parse_prayer_notification_message_body(raw) is None

    def test_rejects_other_event(self):
        raw = json.dumps({"event_type": "GROUP_POST_CREATED", "version": 1, "post_id": "x"})
        assert parse_prayer_notification_message_body(raw) is None

    def test_rejects_missing_prayer_id(self):
        raw = json.dumps({"event_type": PRAYER_RECEIVED_EVENT, "version": 1})
        assert parse_prayer_notification_message_body(raw) is None

    def test_rejects_malformed_json(self):
        assert parse_prayer_notification_message_body("not-json") is None


class TestProcessPrayerNotificationMessage:
    @pytest.mark.asyncio
    @patch(f"{_MODULE}.delete_prayer_notification_message")
    async def test_deletes_malformed_message(self, mock_delete):
        await consumer.process_prayer_notification_message({"ReceiptHandle": "r1", "Body": "x"})
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{_SERVICE}._mark_sent")
    @patch(f"{_SERVICE}._already_sent", return_value=False)
    @patch(f"{_SERVICE}.is_push_configured", return_value=True)
    @patch(f"{_SERVICE}.send_prayer_push_notification", new_callable=AsyncMock)
    @patch(f"{_MODULE}.delete_prayer_notification_message")
    @patch(f"{_SERVICE}._fetch_all_targets", new_callable=AsyncMock)
    @patch(f"{_MODULE}.get_bool", return_value=True)
    async def test_sends_and_deletes_on_success(
        self, _get_bool, mock_fetch, mock_delete, mock_send, _configured, _already, mock_mark
    ):
        prayer_id = uuid4()
        device = ChatPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(prayer_id=prayer_id, devices=[device])

        await consumer.process_prayer_notification_message(
            {"ReceiptHandle": "r1", "Body": _prayer_body(prayer_id)}
        )

        mock_send.assert_awaited_once()
        assert mock_send.await_args.kwargs["prayer_id"] == prayer_id
        mock_mark.assert_called_once_with(prayer_id=prayer_id, push_device_id=device.id)
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{_SERVICE}._already_sent", return_value=False)
    @patch(f"{_SERVICE}.is_push_configured", return_value=True)
    @patch(
        f"{_SERVICE}.send_prayer_push_notification",
        new_callable=AsyncMock,
        side_effect=RuntimeError("fcm down"),
    )
    @patch(f"{_MODULE}.delete_prayer_notification_message")
    @patch(f"{_SERVICE}._fetch_all_targets", new_callable=AsyncMock)
    @patch(f"{_MODULE}.get_bool", return_value=True)
    async def test_transient_failure_leaves_message(
        self, _get_bool, mock_fetch, mock_delete, _send, _configured, _already
    ):
        prayer_id = uuid4()
        device = ChatPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(prayer_id=prayer_id, devices=[device])

        with pytest.raises(service.TransientPrayerNotificationError):
            await consumer.process_prayer_notification_message(
                {"ReceiptHandle": "r1", "Body": _prayer_body(prayer_id)}
            )
        mock_delete.assert_not_called()


class TestPrayerRequestOnPrayerQueue:
    @pytest.mark.asyncio
    @patch(f"{_MODULE}.get_bool", return_value=True)
    @patch(f"{_MODULE}.delete_prayer_notification_message")
    @patch(f"{_MODULE}.process_chat_message_event", new_callable=AsyncMock)
    async def test_prayer_request_is_sent_as_chat_and_deleted_from_prayer_queue(
        self, mock_chat_process, mock_prayer_delete, _get_bool
    ):
        message_id = str(uuid4())
        await consumer.process_prayer_notification_message(
            {
                "ReceiptHandle": "r1",
                "Body": json.dumps(
                    {"event_type": "CHAT_MESSAGE_CREATED", "version": 1, "message_id": message_id}
                ),
            }
        )
        kwargs = mock_chat_process.await_args.kwargs
        assert kwargs["body"]["message_id"] == message_id
        assert kwargs["receipt_handle"] == "r1"
        assert kwargs["delete_message"] is mock_prayer_delete


class TestLegacyPrayerOnChatQueue:
    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.chat_notification_consumer.get_bool", return_value=True)
    @patch("worker_api.notifications.services.chat_notification_consumer.delete_chat_notification_message")
    @patch(
        "worker_api.notifications.services.chat_notification_consumer.process_prayer_event",
        new_callable=AsyncMock,
    )
    async def test_chat_queue_prayer_is_handled_and_deleted_from_chat_queue(
        self, mock_process, mock_chat_delete, _get_bool
    ):
        prayer_id = uuid4()
        await process_chat_notification_message(
            {"ReceiptHandle": "r1", "Body": _prayer_body(prayer_id)}
        )
        kwargs = mock_process.await_args.kwargs
        assert kwargs["body"]["prayer_id"] == str(prayer_id)
        assert kwargs["receipt_handle"] == "r1"
        assert kwargs["delete_message"] is mock_chat_delete

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.chat_notification_consumer.get_bool", return_value=True)
    @patch(
        "worker_api.notifications.services.chat_notification_consumer.process_prayer_event",
        new_callable=AsyncMock,
        side_effect=service.TransientPrayerNotificationError("retry"),
    )
    async def test_chat_queue_prayer_transient_error_is_retried(self, _process, _get_bool):
        with pytest.raises(TransientChatNotificationError):
            await process_chat_notification_message(
                {"ReceiptHandle": "r1", "Body": _prayer_body(uuid4())}
            )


class TestRunPrayerNotificationSqsConsumer:
    @pytest.mark.asyncio
    async def test_processes_received_messages_and_stops(self):
        stop_event = asyncio.Event()
        done = asyncio.Event()
        batches = [[{"MessageId": "p1"}]]

        def fake_receive():
            return batches.pop(0) if batches else []

        async def fake_process(message):
            done.set()

        with patch.object(consumer, "is_prayer_notification_sqs_poll_enabled", return_value=True), \
                patch.object(consumer, "receive_prayer_notification_messages", side_effect=fake_receive), \
                patch.object(consumer, "process_prayer_notification_message", side_effect=fake_process), \
                patch.object(consumer, "get_int", return_value=10):
            runner = asyncio.create_task(consumer.run_prayer_notification_sqs_consumer(stop_event))
            await asyncio.wait_for(done.wait(), timeout=2)
            stop_event.set()
            await asyncio.wait_for(runner, timeout=2)
