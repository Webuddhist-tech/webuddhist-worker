from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from worker_api.notifications.schemas import (
    VerseOfDayNotificationContent,
    VerseOfDayNotificationTargetsResponse,
    VerseOfDayNotificationUserTarget,
    VerseOfDayPushDeviceTarget,
)
from worker_api.notifications.services import verse_of_day_dispatch_service as svc


def _targets(users):
    return VerseOfDayNotificationTargetsResponse(
        generated_at=datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc),
        users=users,
    )


def _user(*, devices):
    return VerseOfDayNotificationUserTarget(
        user_id=uuid4(),
        notification=VerseOfDayNotificationContent(title="WebBuddhist", body="Verse text"),
        push_devices=devices,
    )


class TestDispatchVerseOfDayNotificationsService:
    @pytest.mark.asyncio
    @patch.object(svc, "get_bool", return_value=False)
    async def test_short_circuits_when_dispatch_disabled(self, _get_bool):
        user = _user(devices=[VerseOfDayPushDeviceTarget(token="token-1", platform="android")])
        with patch.object(svc, "get_verse_of_day_notification_targets", new=AsyncMock(return_value=_targets([user]))):
            result = await svc.dispatch_verse_of_day_notifications_service()

        assert result.processed == 0
        assert result.sent == 0
        assert result.failed == 0
        assert result.skipped == 0
        assert result.users == [user]

    @pytest.mark.asyncio
    @patch.object(svc, "get_bool", return_value=True)
    @patch.object(svc, "is_push_configured", return_value=True)
    @patch.object(svc, "send_verse_of_day_push_notification", new_callable=AsyncMock)
    async def test_sends_one_notification_per_device(self, mock_send, _is_configured, _get_bool):
        user = _user(
            devices=[
                VerseOfDayPushDeviceTarget(token="token-1", platform="android"),
                VerseOfDayPushDeviceTarget(token="token-2", platform="ios"),
            ]
        )
        with patch.object(svc, "get_verse_of_day_notification_targets", new=AsyncMock(return_value=_targets([user]))):
            result = await svc.dispatch_verse_of_day_notifications_service()

        assert result.processed == 2
        assert result.sent == 2
        assert result.failed == 0
        assert result.skipped == 0
        assert mock_send.await_count == 2

    @pytest.mark.asyncio
    @patch.object(svc, "get_bool", return_value=True)
    @patch.object(svc, "is_push_configured", return_value=True)
    @patch.object(svc, "send_verse_of_day_push_notification", new_callable=AsyncMock)
    async def test_one_device_failure_does_not_abort_the_loop(self, mock_send, _is_configured, _get_bool):
        mock_send.side_effect = [Exception("fcm failure"), None]
        user = _user(
            devices=[
                VerseOfDayPushDeviceTarget(token="token-1", platform="android"),
                VerseOfDayPushDeviceTarget(token="token-2", platform="android"),
            ]
        )
        with patch.object(svc, "get_verse_of_day_notification_targets", new=AsyncMock(return_value=_targets([user]))):
            result = await svc.dispatch_verse_of_day_notifications_service()

        assert result.processed == 2
        assert result.sent == 1
        assert result.failed == 1

    @pytest.mark.asyncio
    @patch.object(svc, "get_bool", return_value=True)
    @patch.object(svc, "is_push_configured", return_value=False)
    @patch.object(svc, "send_verse_of_day_push_notification", new_callable=AsyncMock)
    async def test_skips_devices_with_unconfigured_platform(self, mock_send, _is_configured, _get_bool):
        user = _user(devices=[VerseOfDayPushDeviceTarget(token="token-1", platform="android")])
        with patch.object(svc, "get_verse_of_day_notification_targets", new=AsyncMock(return_value=_targets([user]))):
            result = await svc.dispatch_verse_of_day_notifications_service()

        assert result.processed == 1
        assert result.skipped == 1
        assert result.sent == 0
        mock_send.assert_not_awaited()
