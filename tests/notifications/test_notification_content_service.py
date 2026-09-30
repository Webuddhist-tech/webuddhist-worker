from unittest.mock import patch
from uuid import uuid4

from worker_api.config import DEFAULTS
from worker_api.notifications.models.reminder_models import UpcomingReminder
from worker_api.notifications.services.notification_content_service import build_notification_content


class TestBuildNotificationContent:
    def test_default_body(self):
        reminder = UpcomingReminder(
            id=uuid4(),
            user_id=uuid4(),
            plan_id=uuid4(),
            trigger_at=None,
            timezone="UTC",
            status="pending",
            device_token="token",
            platform="android",
            routine_config={},
        )
        title, body = build_notification_content(reminder)
        assert title == DEFAULTS["NOTIFICATION_DEFAULT_TITLE"]
        assert body in DEFAULTS["NOTIFICATION_DEFAULT_BODIES"]

    def test_plan_name_template(self):
        reminder = UpcomingReminder(
            id=uuid4(),
            user_id=uuid4(),
            plan_id=uuid4(),
            trigger_at=None,
            timezone="UTC",
            status="pending",
            device_token="token",
            platform="android",
            routine_config={"plan_name": "Morning Meditation"},
        )
        _, body = build_notification_content(reminder)
        assert body == "Time for Morning Meditation"

    def test_custom_message_template(self):
        reminder = UpcomingReminder(
            id=uuid4(),
            user_id=uuid4(),
            plan_id=uuid4(),
            trigger_at=None,
            timezone="UTC",
            status="pending",
            device_token="token",
            platform="android",
            routine_config={"message_template": "Custom reminder text"},
        )
        _, body = build_notification_content(reminder)
        assert body == "Custom reminder text"
