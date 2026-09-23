"""Per-device outcomes when an announcement is delivered, and what each one
means for the SQS message.

The happy path lives in test_event_announcement_consumer.py. This covers the
branches that decide whether the message is deleted or left to retry, which
is where a wrong answer costs either a lost announcement or a duplicate one.
"""
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from worker_api.notifications.schemas import (
    EventAnnouncementTargetsResponse,
    EventNotificationRecipient,
    EventPushDeviceTarget,
)
from worker_api.notifications.services.event_notification_consumer import (
    TransientEventNotificationError,
    process_event_notification_message,
)
from worker_api.notifications.services.push.fcm_client import PermanentPushTokenError

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


def _targets(*, event_id, devices):
    return EventAnnouncementTargetsResponse(
        event_id=event_id,
        audience="participants",
        recipients=[
            EventNotificationRecipient(user_id=uuid4(), push_devices=devices)
        ],
        skip=0,
        limit=100,
        total=1,
        has_more=False,
    )


def _device(platform="android"):
    return EventPushDeviceTarget(id=uuid4(), token="tok", platform=platform)


class TestDeviceOutcomes:
    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_event_notification_message")
    @patch(
        f"{CONSUMER}.send_event_announcement_push_notification",
        new_callable=AsyncMock,
    )
    @patch(f"{CONSUMER}._fetch_all_announcement_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.is_push_configured", return_value=False)
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_a_platform_without_push_credentials_is_skipped(
        self, _get_bool, _get_int, _configured, mock_fetch, mock_send, mock_delete
    ):
        """Skipped rather than failed: no amount of retrying will configure
        credentials that were never set up."""
        event_id = uuid4()
        mock_fetch.return_value = _targets(event_id=event_id, devices=[_device("ios")])

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _announcement_body(event_id)}
        )

        mock_send.assert_not_awaited()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_event_notification_message")
    @patch(
        f"{CONSUMER}.send_event_announcement_push_notification",
        new_callable=AsyncMock,
    )
    @patch(f"{CONSUMER}._fetch_all_announcement_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.is_push_configured", return_value=True)
    @patch(f"{CONSUMER}._already_sent", return_value=True)
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_a_device_already_reached_is_not_sent_to_again(
        self,
        _get_bool,
        _get_int,
        _already,
        _configured,
        mock_fetch,
        mock_send,
        mock_delete,
    ):
        """The redelivery case: SQS handed back a message that another
        device's failure had kept alive."""
        event_id = uuid4()
        mock_fetch.return_value = _targets(event_id=event_id, devices=[_device()])

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _announcement_body(event_id)}
        )

        mock_send.assert_not_awaited()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_event_notification_message")
    @patch(f"{CONSUMER}.deactivate_push_device", new_callable=AsyncMock)
    @patch(
        f"{CONSUMER}.send_event_announcement_push_notification",
        new_callable=AsyncMock,
        side_effect=PermanentPushTokenError("unregistered"),
    )
    @patch(f"{CONSUMER}._fetch_all_announcement_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.is_push_configured", return_value=True)
    @patch(f"{CONSUMER}._already_sent", return_value=False)
    @patch(f"{CONSUMER}._mark_sent")
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_a_dead_token_is_deactivated_and_not_retried(
        self,
        _get_bool,
        _get_int,
        mock_mark,
        _already,
        _configured,
        mock_fetch,
        _send,
        mock_deactivate,
        mock_delete,
    ):
        """Marked sent although nothing arrived: the token is gone for good,
        so a retry would only deactivate it a second time."""
        event_id = uuid4()
        device = _device()
        mock_fetch.return_value = _targets(event_id=event_id, devices=[device])

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _announcement_body(event_id)}
        )

        mock_deactivate.assert_awaited_once_with(push_device_id=device.id)
        mock_mark.assert_called_once()
        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_event_notification_message")
    @patch(
        f"{CONSUMER}.deactivate_push_device",
        new_callable=AsyncMock,
        side_effect=RuntimeError("backend down"),
    )
    @patch(
        f"{CONSUMER}.send_event_announcement_push_notification",
        new_callable=AsyncMock,
        side_effect=PermanentPushTokenError("unregistered"),
    )
    @patch(f"{CONSUMER}._fetch_all_announcement_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.is_push_configured", return_value=True)
    @patch(f"{CONSUMER}._already_sent", return_value=False)
    @patch(f"{CONSUMER}._mark_sent")
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_a_failed_deactivation_still_settles_the_message(
        self,
        _get_bool,
        _get_int,
        _mark,
        _already,
        _configured,
        mock_fetch,
        _send,
        _deactivate,
        mock_delete,
    ):
        """Housekeeping that failed must not resurrect a dead token's
        announcement - the push is unrecoverable either way."""
        event_id = uuid4()
        mock_fetch.return_value = _targets(event_id=event_id, devices=[_device()])

        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _announcement_body(event_id)}
        )

        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_event_notification_message")
    @patch(
        f"{CONSUMER}.send_event_announcement_push_notification",
        new_callable=AsyncMock,
        side_effect=RuntimeError("FCM unavailable"),
    )
    @patch(f"{CONSUMER}._fetch_all_announcement_targets", new_callable=AsyncMock)
    @patch(f"{CONSUMER}.is_push_configured", return_value=True)
    @patch(f"{CONSUMER}._already_sent", return_value=False)
    @patch(f"{CONSUMER}._mark_sent")
    @patch(f"{CONSUMER}.get_int", return_value=5)
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_a_transient_fcm_failure_keeps_the_message(
        self,
        _get_bool,
        _get_int,
        mock_mark,
        _already,
        _configured,
        mock_fetch,
        _send,
        mock_delete,
    ):
        event_id = uuid4()
        mock_fetch.return_value = _targets(event_id=event_id, devices=[_device()])

        with pytest.raises(TransientEventNotificationError):
            await process_event_notification_message(
                {"ReceiptHandle": "r1", "Body": _announcement_body(event_id)}
            )

        # Nothing recorded as sent, so the retry tries this device again
        # rather than skipping it.
        mock_mark.assert_not_called()
        mock_delete.assert_not_called()


class TestTargetLookupFailures:
    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_event_notification_message")
    @patch(
        f"{CONSUMER}._fetch_all_announcement_targets",
        new_callable=AsyncMock,
        side_effect=HTTPException(status_code=404, detail="Event not found"),
    )
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_a_deleted_event_drops_the_announcement(
        self, _get_bool, _fetch, mock_delete
    ):
        """There is nothing left to announce, so retrying until the queue
        gives up would only delay the inevitable."""
        await process_event_notification_message(
            {"ReceiptHandle": "r1", "Body": _announcement_body(uuid4())}
        )

        mock_delete.assert_called_once_with("r1")

    @pytest.mark.asyncio
    @patch(f"{CONSUMER}.delete_event_notification_message")
    @patch(
        f"{CONSUMER}._fetch_all_announcement_targets",
        new_callable=AsyncMock,
        side_effect=HTTPException(status_code=503, detail="Backend unavailable"),
    )
    @patch(f"{CONSUMER}.get_bool", return_value=True)
    async def test_an_unavailable_backend_keeps_the_announcement(
        self, _get_bool, _fetch, mock_delete
    ):
        with pytest.raises(TransientEventNotificationError):
            await process_event_notification_message(
                {"ReceiptHandle": "r1", "Body": _announcement_body(uuid4())}
            )

        mock_delete.assert_not_called()
