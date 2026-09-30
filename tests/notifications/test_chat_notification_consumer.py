"""Tests for chat notification SQS consumer."""
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from worker_api.notifications.schemas import (
    ChatNotificationRecipient,
    ChatNotificationTargetsResponse,
    ChatPushDeviceTarget,
)
from worker_api.notifications.services.chat_notification_consumer import (
    TransientChatNotificationError,
    process_chat_notification_message,
)
from worker_api.notifications.services.push.fcm_client import PermanentPushTokenError


def _targets(*, message_id, devices):
    return ChatNotificationTargetsResponse(
        message_id=message_id,
        room_id=uuid4(),
        sender_id=uuid4(),
        chat_kind="PRIVATE",
        group_id=None,
        title="Alice",
        body="Hello",
        recipients=[
            ChatNotificationRecipient(
                user_id=uuid4(),
                push_devices=devices,
            )
        ],
        skip=0,
        limit=100,
        total=1,
        has_more=False,
    )


class TestProcessChatNotificationMessage:
    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.chat_notification_consumer.delete_chat_notification_message")
    async def test_deletes_malformed_message(self, mock_delete):
        await process_chat_notification_message(
            {"ReceiptHandle": "r1", "Body": "not-json"}
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.chat_notification_consumer.delete_chat_notification_message")
    @patch(
        "worker_api.notifications.services.chat_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch("worker_api.notifications.services.chat_notification_consumer.get_bool", return_value=True)
    async def test_deletes_when_message_not_found(self, _get_bool, mock_fetch, mock_delete):
        from fastapi import HTTPException

        mock_fetch.side_effect = HTTPException(status_code=404, detail="not found")
        message_id = uuid4()
        await process_chat_notification_message(
            {
                "ReceiptHandle": "r1",
                "Body": json.dumps(
                    {
                        "event_type": "CHAT_MESSAGE_CREATED",
                        "version": 1,
                        "message_id": str(message_id),
                    }
                ),
            }
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.chat_notification_consumer.delete_chat_notification_message")
    @patch(
        "worker_api.notifications.services.chat_notification_consumer.send_chat_push_notification",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer._already_sent",
        return_value=False,
    )
    @patch("worker_api.notifications.services.chat_notification_consumer._mark_sent")
    @patch("worker_api.notifications.services.chat_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.chat_notification_consumer.get_bool", return_value=True)
    async def test_sends_and_deletes_on_success(
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
        message_id = uuid4()
        device = ChatPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(message_id=message_id, devices=[device])

        await process_chat_notification_message(
            {
                "ReceiptHandle": "r1",
                "Body": json.dumps(
                    {
                        "event_type": "CHAT_MESSAGE_CREATED",
                        "version": 1,
                        "message_id": str(message_id),
                    }
                ),
            }
        )

        mock_send.assert_awaited_once()
        mock_mark.assert_called_once()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.chat_notification_consumer.delete_chat_notification_message")
    @patch(
        "worker_api.notifications.services.chat_notification_consumer.deactivate_push_device",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer.send_chat_push_notification",
        new_callable=AsyncMock,
        side_effect=PermanentPushTokenError("gone"),
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer._already_sent",
        return_value=False,
    )
    @patch("worker_api.notifications.services.chat_notification_consumer._mark_sent")
    @patch("worker_api.notifications.services.chat_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.chat_notification_consumer.get_bool", return_value=True)
    async def test_permanent_token_deactivates_and_deletes(
        self,
        _get_bool,
        _get_int,
        mock_mark,
        _already,
        _configured,
        mock_fetch,
        mock_send,
        mock_deactivate,
        mock_delete,
    ):
        message_id = uuid4()
        device = ChatPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(message_id=message_id, devices=[device])

        await process_chat_notification_message(
            {
                "ReceiptHandle": "r1",
                "Body": json.dumps(
                    {
                        "event_type": "CHAT_MESSAGE_CREATED",
                        "version": 1,
                        "message_id": str(message_id),
                    }
                ),
            }
        )

        mock_deactivate.assert_awaited_once_with(push_device_id=device.id)
        mock_mark.assert_called_once()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.chat_notification_consumer.delete_chat_notification_message")
    @patch(
        "worker_api.notifications.services.chat_notification_consumer.send_chat_push_notification",
        new_callable=AsyncMock,
        side_effect=RuntimeError("temporary"),
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer._already_sent",
        return_value=False,
    )
    @patch("worker_api.notifications.services.chat_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.chat_notification_consumer.get_bool", return_value=True)
    async def test_transient_failure_leaves_message(
        self,
        _get_bool,
        _get_int,
        _already,
        _configured,
        mock_fetch,
        mock_send,
        mock_delete,
    ):
        message_id = uuid4()
        device = ChatPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(message_id=message_id, devices=[device])

        with pytest.raises(TransientChatNotificationError):
            await process_chat_notification_message(
                {
                    "ReceiptHandle": "r1",
                    "Body": json.dumps(
                        {
                            "event_type": "CHAT_MESSAGE_CREATED",
                            "version": 1,
                            "message_id": str(message_id),
                        }
                    ),
                }
            )

        mock_delete.assert_not_called()

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.chat_notification_consumer.delete_chat_notification_message")
    @patch(
        "worker_api.notifications.services.chat_notification_consumer.send_chat_push_notification",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.chat_notification_consumer._already_sent",
        return_value=True,
    )
    @patch("worker_api.notifications.services.chat_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.chat_notification_consumer.get_bool", return_value=True)
    async def test_skips_already_sent_devices(
        self,
        _get_bool,
        _get_int,
        _already,
        _configured,
        mock_fetch,
        mock_send,
        mock_delete,
    ):
        message_id = uuid4()
        device = ChatPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(message_id=message_id, devices=[device])

        await process_chat_notification_message(
            {
                "ReceiptHandle": "r1",
                "Body": json.dumps(
                    {
                        "event_type": "CHAT_MESSAGE_CREATED",
                        "version": 1,
                        "message_id": str(message_id),
                    }
                ),
            }
        )

        mock_send.assert_not_called()
        mock_delete.assert_called_once_with("r1")


class TestRunChatNotificationSqsConsumer:
    @pytest.mark.asyncio
    async def test_slow_message_does_not_block_later_message(self):
        import asyncio

        from worker_api.notifications.services import chat_notification_consumer as consumer

        stop_event = asyncio.Event()
        release_slow = asyncio.Event()
        fast_done = asyncio.Event()
        batches = [[{"MessageId": "slow"}, {"MessageId": "fast"}]]

        def fake_receive():
            return batches.pop(0) if batches else []

        async def fake_process(message):
            if message["MessageId"] == "slow":
                await release_slow.wait()
            else:
                fast_done.set()

        with patch.object(consumer, "is_chat_notification_sqs_poll_enabled", return_value=True), \
                patch.object(consumer, "receive_chat_notification_messages", side_effect=fake_receive), \
                patch.object(consumer, "process_chat_notification_message", side_effect=fake_process), \
                patch.object(consumer, "get_int", return_value=10):
            runner = asyncio.create_task(consumer.run_chat_notification_sqs_consumer(stop_event))
            # The fast message finishes while the slow one is still in flight.
            await asyncio.wait_for(fast_done.wait(), timeout=2)
            stop_event.set()
            release_slow.set()
            await asyncio.wait_for(runner, timeout=2)
