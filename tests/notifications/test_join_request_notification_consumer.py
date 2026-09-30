"""Tests for join request notification SQS consumer."""
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from worker_api.notifications.schemas import (
    JoinRequestNotificationRecipient,
    JoinRequestNotificationTargetsResponse,
    JoinRequestPushDeviceTarget,
)
from worker_api.notifications.services.join_request_notification_consumer import (
    TransientJoinRequestNotificationError,
    _idempotency_key,
    process_join_request_notification_message,
)
from worker_api.notifications.services.push.fcm_client import PermanentPushTokenError

CONSUMER = "worker_api.notifications.services.join_request_notification_consumer"


def _targets(
    *,
    join_request_id,
    devices,
    event_type="JOIN_REQUEST_CREATED",
    status="PENDING",
    recipients=None,
):
    if recipients is None:
        recipients = [
            JoinRequestNotificationRecipient(user_id=uuid4(), push_devices=devices)
        ]
    return JoinRequestNotificationTargetsResponse(
        join_request_id=join_request_id,
        group_id=uuid4(),
        event_type=event_type,
        status=status,
        group_name="Morning Sangha",
        requester_name="Tenzin Tib",
        title="Morning Sangha",
        body="Tenzin Tib asked to join Morning Sangha",
        recipients=recipients,
        skip=0,
        limit=100,
        total=1,
        has_more=False,
    )


def _sqs_message(*, join_request_id, event_type="JOIN_REQUEST_CREATED", receipt="r1"):
    return {
        "ReceiptHandle": receipt,
        "Body": json.dumps(
            {
                "event_type": event_type,
                "version": 1,
                "join_request_id": str(join_request_id),
            }
        ),
    }


class TestProcessJoinRequestNotificationMessage:
    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    async def test_deletes_malformed_message(self, mock_delete):
        await process_join_request_notification_message(
            {"ReceiptHandle": "r1", "Body": "not-json"}
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    async def test_deletes_unknown_event_type(self, mock_delete):
        await process_join_request_notification_message(
            {
                "ReceiptHandle": "r1",
                "Body": json.dumps(
                    {
                        "event_type": "SOMETHING_ELSE",
                        "version": 1,
                        "join_request_id": str(uuid4()),
                    }
                ),
            }
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    async def test_deletes_wrong_version(self, mock_delete):
        await process_join_request_notification_message(
            {
                "ReceiptHandle": "r1",
                "Body": json.dumps(
                    {
                        "event_type": "JOIN_REQUEST_CREATED",
                        "version": 2,
                        "join_request_id": str(uuid4()),
                    }
                ),
            }
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    @patch(f"{CONSUMER}._fetch_all_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.get_bool", return_value=False)
    async def test_deletes_when_dispatch_disabled(self, _get_bool, mock_fetch, mock_delete):
        await process_join_request_notification_message(
            _sqs_message(join_request_id=uuid4())
        )
        mock_fetch.assert_not_awaited()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    @patch(f"{CONSUMER}._fetch_all_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_deletes_when_join_request_not_found(self, _get_bool, mock_fetch, mock_delete):
        from fastapi import HTTPException

        mock_fetch.side_effect = HTTPException(status_code=404, detail="not found")
        await process_join_request_notification_message(
            _sqs_message(join_request_id=uuid4())
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    @patch(f"{CONSUMER}.send_join_request_push_notification", new_callable=AsyncMock)
    @patch(f"{CONSUMER}._fetch_all_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_deletes_when_no_recipients(
        self, _get_bool, _get_int, mock_fetch, mock_send, mock_delete
    ):
        join_request_id = uuid4()
        mock_fetch.return_value = _targets(
            join_request_id=join_request_id, devices=[], recipients=[]
        )

        await process_join_request_notification_message(
            _sqs_message(join_request_id=join_request_id)
        )

        mock_send.assert_not_called()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    @patch(f"{CONSUMER}.send_join_request_push_notification", new_callable=AsyncMock)
    @patch(f"{CONSUMER}._fetch_all_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_deletes_when_recipient_has_no_devices(
        self, _get_bool, _get_int, mock_fetch, mock_send, mock_delete
    ):
        join_request_id = uuid4()
        mock_fetch.return_value = _targets(join_request_id=join_request_id, devices=[])

        await process_join_request_notification_message(
            _sqs_message(join_request_id=join_request_id)
        )

        mock_send.assert_not_called()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    @patch(f"{CONSUMER}.send_join_request_push_notification", new_callable=AsyncMock)
    @patch(f"{CONSUMER}._fetch_all_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.is_push_configured", return_value=True)
    @patch(f"{CONSUMER}._already_sent", return_value=False)
    @patch(f"{CONSUMER}._mark_sent")
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_sends_created_event_and_deletes(
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
        join_request_id = uuid4()
        device = JoinRequestPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        targets = _targets(
            join_request_id=join_request_id,
            devices=[device],
            event_type="JOIN_REQUEST_CREATED",
            status="PENDING",
        )
        mock_fetch.return_value = targets

        await process_join_request_notification_message(
            _sqs_message(join_request_id=join_request_id, event_type="JOIN_REQUEST_CREATED")
        )

        mock_send.assert_awaited_once_with(
            device_token="tok",
            event_type="JOIN_REQUEST_CREATED",
            join_request_id=join_request_id,
            group_id=targets.group_id,
            status="PENDING",
            title=targets.title,
            body=targets.body,
        )
        mock_mark.assert_called_once()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    @patch(f"{CONSUMER}.send_join_request_push_notification", new_callable=AsyncMock)
    @patch(f"{CONSUMER}._fetch_all_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.is_push_configured", return_value=True)
    @patch(f"{CONSUMER}._already_sent", return_value=False)
    @patch(f"{CONSUMER}._mark_sent")
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_sends_decided_event_and_deletes(
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
        join_request_id = uuid4()
        device = JoinRequestPushDeviceTarget(id=uuid4(), token="tok", platform="ios")
        targets = _targets(
            join_request_id=join_request_id,
            devices=[device],
            event_type="JOIN_REQUEST_DECIDED",
            status="APPROVED",
        )
        mock_fetch.return_value = targets

        await process_join_request_notification_message(
            _sqs_message(join_request_id=join_request_id, event_type="JOIN_REQUEST_DECIDED")
        )

        assert mock_send.await_args.kwargs["event_type"] == "JOIN_REQUEST_DECIDED"
        assert mock_send.await_args.kwargs["status"] == "APPROVED"
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    @patch(f"{CONSUMER}.deactivate_push_device", new_callable=AsyncMock)
    @patch(
        f"{CONSUMER}.send_join_request_push_notification",
        new_callable=AsyncMock,
        side_effect=PermanentPushTokenError("gone"),
    )
    @patch(f"{CONSUMER}._fetch_all_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.is_push_configured", return_value=True)
    @patch(f"{CONSUMER}._already_sent", return_value=False)
    @patch(f"{CONSUMER}._mark_sent")
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
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
        join_request_id = uuid4()
        device = JoinRequestPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(join_request_id=join_request_id, devices=[device])

        await process_join_request_notification_message(
            _sqs_message(join_request_id=join_request_id)
        )

        mock_deactivate.assert_awaited_once_with(push_device_id=device.id)
        mock_mark.assert_called_once()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    @patch(
        f"{CONSUMER}.send_join_request_push_notification",
        new_callable=AsyncMock,
        side_effect=RuntimeError("temporary"),
    )
    @patch(f"{CONSUMER}._fetch_all_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.is_push_configured", return_value=True)
    @patch(f"{CONSUMER}._already_sent", return_value=False)
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
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
        join_request_id = uuid4()
        device = JoinRequestPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(join_request_id=join_request_id, devices=[device])

        with pytest.raises(TransientJoinRequestNotificationError):
            await process_join_request_notification_message(
                _sqs_message(join_request_id=join_request_id)
            )

        mock_delete.assert_not_called()

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    @patch(f"{CONSUMER}.send_join_request_push_notification", new_callable=AsyncMock)
    @patch(f"{CONSUMER}._fetch_all_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.is_push_configured", return_value=True)
    @patch(f"{CONSUMER}._already_sent", return_value=True)
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
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
        join_request_id = uuid4()
        device = JoinRequestPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(join_request_id=join_request_id, devices=[device])

        await process_join_request_notification_message(
            _sqs_message(join_request_id=join_request_id)
        )

        mock_send.assert_not_called()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_join_request_notification_message")
    @patch(f"{CONSUMER}.send_join_request_push_notification", new_callable=AsyncMock)
    @patch(f"{CONSUMER}._fetch_all_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.is_push_configured", return_value=False)
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_skips_when_push_not_configured(
        self,
        _get_bool,
        _get_int,
        _configured,
        mock_fetch,
        mock_send,
        mock_delete,
    ):
        join_request_id = uuid4()
        device = JoinRequestPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(join_request_id=join_request_id, devices=[device])

        await process_join_request_notification_message(
            _sqs_message(join_request_id=join_request_id)
        )

        mock_send.assert_not_called()
        mock_delete.assert_called_once_with("r1")


class TestIdempotencyKey:
    @patch(f"{CONSUMER}.get", return_value="prefix:")
    def test_key_separates_event_types(self, _get):
        join_request_id = uuid4()
        push_device_id = uuid4()
        created = _idempotency_key(
            join_request_id=join_request_id,
            event_type="JOIN_REQUEST_CREATED",
            push_device_id=push_device_id,
        )
        decided = _idempotency_key(
            join_request_id=join_request_id,
            event_type="JOIN_REQUEST_DECIDED",
            push_device_id=push_device_id,
        )
        assert created != decided

    @patch(f"{CONSUMER}.get", return_value="prefix:")
    def test_key_separates_devices(self, _get):
        join_request_id = uuid4()
        first = _idempotency_key(
            join_request_id=join_request_id,
            event_type="JOIN_REQUEST_CREATED",
            push_device_id=uuid4(),
        )
        second = _idempotency_key(
            join_request_id=join_request_id,
            event_type="JOIN_REQUEST_CREATED",
            push_device_id=uuid4(),
        )
        assert first != second
