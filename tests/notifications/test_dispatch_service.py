from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from worker_api.notifications.enums import PushPlatform
from worker_api.notifications.models.reminder_models import UpcomingReminder
from worker_api.notifications.schemas import NotificationContent
from worker_api.notifications.services import dispatch_service


def _reminder() -> UpcomingReminder:
    return UpcomingReminder(
        id=uuid4(),
        user_id=uuid4(),
        plan_id=uuid4(),
        trigger_at=datetime.now(timezone.utc),
        timezone="Asia/Kolkata",
        device_token="device-token",
        platform=PushPlatform.ANDROID,
        routine_config={"times": ["06:00"]},
    )


@pytest.fixture
def db_session():
    session = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = session
    context.__exit__.return_value = False
    with patch.object(dispatch_service, "SessionLocal", return_value=context):
        yield session


@pytest.fixture
def repository():
    with patch.object(dispatch_service, "reminder_repository") as mock_repository:
        yield mock_repository


class TestDispatchDueNotificationsService:
    @pytest.mark.asyncio
    async def test_returns_zero_counts_when_dispatch_disabled(self, db_session, repository):
        with patch.object(dispatch_service, "get_bool", return_value=False):
            result = await dispatch_service.dispatch_due_notifications_service()

        assert result.model_dump() == {"processed": 0, "sent": 0, "failed": 0, "skipped": 0}
        repository.get_due_reminders.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_reminders_already_dispatched(self, db_session, repository):
        repository.get_due_reminders.return_value = [_reminder()]

        with patch.object(dispatch_service, "get_bool", return_value=True), patch.object(
            dispatch_service, "get_int", return_value=100
        ), patch.object(dispatch_service, "_already_dispatched", return_value=True), patch.object(
            dispatch_service, "send_push_notification", new_callable=AsyncMock
        ) as mock_send:
            result = await dispatch_service.dispatch_due_notifications_service()

        assert result.processed == 1
        assert result.skipped == 1
        assert result.sent == 0
        mock_send.assert_not_awaited()
        db_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_sends_marks_and_reschedules_due_reminder(self, db_session, repository):
        reminder = _reminder()
        next_trigger = datetime.now(timezone.utc) + timedelta(days=1)
        repository.get_due_reminders.return_value = [reminder]

        with patch.object(dispatch_service, "get_bool", return_value=True), patch.object(
            dispatch_service, "get_int", return_value=100
        ), patch.object(dispatch_service, "_already_dispatched", return_value=False), patch.object(
            dispatch_service, "_mark_dispatched"
        ) as mock_mark_dispatched, patch.object(
            dispatch_service,
            "resolve_notification_content",
            new_callable=AsyncMock,
            return_value=NotificationContent(title="Day 1", body="Practice", image_url=None),
        ), patch.object(
            dispatch_service, "send_push_notification", new_callable=AsyncMock
        ) as mock_send, patch.object(
            dispatch_service, "compute_next_trigger_at", return_value=next_trigger
        ):
            result = await dispatch_service.dispatch_due_notifications_service()

        assert result.processed == 1
        assert result.sent == 1
        assert result.failed == 0
        mock_send.assert_awaited_once_with(reminder, "Day 1", "Practice", None)
        repository.mark_sent.assert_called_once_with(db_session, reminder)
        assert repository.create_reminder.call_args.kwargs["trigger_at"] == next_trigger
        mock_mark_dispatched.assert_called_once_with(reminder.id)

    @pytest.mark.asyncio
    async def test_counts_failure_when_send_raises(self, db_session, repository):
        repository.get_due_reminders.return_value = [_reminder()]

        with patch.object(dispatch_service, "get_bool", return_value=True), patch.object(
            dispatch_service, "get_int", return_value=100
        ), patch.object(dispatch_service, "_already_dispatched", return_value=False), patch.object(
            dispatch_service, "_mark_dispatched"
        ) as mock_mark_dispatched, patch.object(
            dispatch_service,
            "resolve_notification_content",
            new_callable=AsyncMock,
            return_value=NotificationContent(title="Day 1", body="Practice"),
        ), patch.object(
            dispatch_service,
            "send_push_notification",
            new_callable=AsyncMock,
            side_effect=RuntimeError("fcm down"),
        ):
            result = await dispatch_service.dispatch_due_notifications_service()

        assert result.processed == 1
        assert result.failed == 1
        assert result.sent == 0
        repository.mark_sent.assert_not_called()
        mock_mark_dispatched.assert_not_called()
