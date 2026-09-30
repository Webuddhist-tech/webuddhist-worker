"""Tests for group post notification SQS consumer."""
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from worker_api.notifications.schemas import (
    GroupPostNotificationRecipient,
    GroupPostNotificationTargetsResponse,
    GroupPostPushDeviceTarget,
)
from worker_api.notifications.services.group_post_notification_consumer import (
    TransientGroupPostNotificationError,
    process_group_post_notification_message,
)
from worker_api.notifications.services.push.fcm_client import PermanentPushTokenError


def _targets(*, post_id, devices):
    return GroupPostNotificationTargetsResponse(
        post_id=post_id,
        group_id=uuid4(),
        author_id=uuid4(),
        title="Sangha",
        body="Alice shared a new post",
        recipients=[
            GroupPostNotificationRecipient(
                user_id=uuid4(),
                push_devices=devices,
            )
        ],
        skip=0,
        limit=100,
        total=1,
        has_more=False,
    )


def _body(post_id):
    return json.dumps(
        {
            "event_type": "GROUP_POST_CREATED",
            "version": 1,
            "post_id": str(post_id),
        }
    )


class TestProcessGroupPostNotificationMessage:
    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.group_post_notification_consumer.delete_group_post_notification_message")
    async def test_deletes_malformed_message(self, mock_delete):
        await process_group_post_notification_message(
            {"ReceiptHandle": "r1", "Body": "not-json"}
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.group_post_notification_consumer.delete_group_post_notification_message")
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch("worker_api.notifications.services.group_post_notification_consumer.get_bool", return_value=True)
    async def test_deletes_when_post_not_found(self, _get_bool, mock_fetch, mock_delete):
        from fastapi import HTTPException

        mock_fetch.side_effect = HTTPException(status_code=404, detail="not found")
        post_id = uuid4()
        await process_group_post_notification_message(
            {"ReceiptHandle": "r1", "Body": _body(post_id)}
        )
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.group_post_notification_consumer.delete_group_post_notification_message")
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer.send_group_post_push_notification",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer._already_sent",
        return_value=False,
    )
    @patch("worker_api.notifications.services.group_post_notification_consumer._mark_sent")
    @patch("worker_api.notifications.services.group_post_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.group_post_notification_consumer.get_bool", return_value=True)
    async def test_sends_and_deletes_on_success(
        self, _get_bool, _get_int, mock_mark, _already, _configured, mock_fetch, mock_send, mock_delete,
    ):
        post_id = uuid4()
        device = GroupPostPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(post_id=post_id, devices=[device])

        await process_group_post_notification_message(
            {"ReceiptHandle": "r1", "Body": _body(post_id)}
        )

        mock_send.assert_awaited_once()
        mock_mark.assert_called_once()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.group_post_notification_consumer.delete_group_post_notification_message")
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer.deactivate_push_device",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer.send_group_post_push_notification",
        new_callable=AsyncMock,
        side_effect=PermanentPushTokenError("gone"),
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer._already_sent",
        return_value=False,
    )
    @patch("worker_api.notifications.services.group_post_notification_consumer._mark_sent")
    @patch("worker_api.notifications.services.group_post_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.group_post_notification_consumer.get_bool", return_value=True)
    async def test_permanent_token_deactivates_and_deletes(
        self, _get_bool, _get_int, mock_mark, _already, _configured, mock_fetch, mock_send, mock_deactivate, mock_delete,
    ):
        post_id = uuid4()
        device = GroupPostPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(post_id=post_id, devices=[device])

        await process_group_post_notification_message(
            {"ReceiptHandle": "r1", "Body": _body(post_id)}
        )

        mock_deactivate.assert_awaited_once_with(push_device_id=device.id)
        mock_mark.assert_called_once()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.group_post_notification_consumer.delete_group_post_notification_message")
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer.send_group_post_push_notification",
        new_callable=AsyncMock,
        side_effect=RuntimeError("temporary"),
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer._already_sent",
        return_value=False,
    )
    @patch("worker_api.notifications.services.group_post_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.group_post_notification_consumer.get_bool", return_value=True)
    async def test_transient_failure_leaves_message(
        self, _get_bool, _get_int, _already, _configured, mock_fetch, mock_send, mock_delete,
    ):
        post_id = uuid4()
        device = GroupPostPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(post_id=post_id, devices=[device])

        with pytest.raises(TransientGroupPostNotificationError):
            await process_group_post_notification_message(
                {"ReceiptHandle": "r1", "Body": _body(post_id)}
            )

        mock_delete.assert_not_called()

    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.group_post_notification_consumer.delete_group_post_notification_message")
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer.send_group_post_push_notification",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer._fetch_all_targets",
        new_callable=AsyncMock,
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer.is_push_configured",
        return_value=True,
    )
    @patch(
        "worker_api.notifications.services.group_post_notification_consumer._already_sent",
        return_value=True,
    )
    @patch("worker_api.notifications.services.group_post_notification_consumer.get_int", return_value=5)
    @patch("worker_api.notifications.services.group_post_notification_consumer.get_bool", return_value=True)
    async def test_skips_already_sent_devices(
        self, _get_bool, _get_int, _already, _configured, mock_fetch, mock_send, mock_delete,
    ):
        post_id = uuid4()
        device = GroupPostPushDeviceTarget(id=uuid4(), token="tok", platform="android")
        mock_fetch.return_value = _targets(post_id=post_id, devices=[device])

        await process_group_post_notification_message(
            {"ReceiptHandle": "r1", "Body": _body(post_id)}
        )

        mock_send.assert_not_called()
        mock_delete.assert_called_once_with("r1")
