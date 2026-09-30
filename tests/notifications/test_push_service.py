from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from worker_api.notifications.enums import PushPlatform
from worker_api.notifications.models.reminder_models import UpcomingReminder
from worker_api.notifications.services import push_service


@pytest.fixture(autouse=True)
def reset_redis_client():
    push_service._redis_client = None
    yield
    push_service._redis_client = None


def _reminder(platform: str = PushPlatform.ANDROID) -> UpcomingReminder:
    return UpcomingReminder(
        id=uuid4(),
        user_id=uuid4(),
        plan_id=uuid4(),
        timezone="Asia/Kolkata",
        device_token="device-token",
        platform=platform,
        routine_config={"times": ["06:00"]},
    )


class TestGetRedisClient:
    def test_reuses_single_client_instance(self):
        with patch.object(push_service.redis.Redis, "from_url") as mock_from_url:
            first = push_service._get_redis_client()
            second = push_service._get_redis_client()

        assert first is second
        mock_from_url.assert_called_once()


class TestIdempotencyKey:
    def test_prefixes_reminder_id(self):
        reminder_id = uuid4()
        with patch.object(push_service, "get", return_value="worker:notifications:sent:"):
            key = push_service._idempotency_key(reminder_id)
        assert key == f"worker:notifications:sent:{reminder_id}"


class TestAlreadyDispatched:
    def test_returns_false_when_idempotency_disabled(self):
        with patch.object(push_service, "get_bool", return_value=False):
            with patch.object(push_service, "_get_redis_client") as mock_client:
                assert push_service._already_dispatched(uuid4()) is False
        mock_client.assert_not_called()

    def test_returns_true_when_key_exists(self):
        client = MagicMock()
        client.exists.return_value = 1
        with patch.object(push_service, "get_bool", return_value=True), patch.object(
            push_service, "_get_redis_client", return_value=client
        ), patch.object(push_service, "get", return_value="prefix:"):
            assert push_service._already_dispatched(uuid4()) is True

    def test_returns_false_when_key_absent(self):
        client = MagicMock()
        client.exists.return_value = 0
        with patch.object(push_service, "get_bool", return_value=True), patch.object(
            push_service, "_get_redis_client", return_value=client
        ), patch.object(push_service, "get", return_value="prefix:"):
            assert push_service._already_dispatched(uuid4()) is False


class TestMarkDispatched:
    def test_skips_write_when_idempotency_disabled(self):
        with patch.object(push_service, "get_bool", return_value=False):
            with patch.object(push_service, "_get_redis_client") as mock_client:
                push_service._mark_dispatched(uuid4())
        mock_client.assert_not_called()

    def test_writes_key_with_configured_ttl(self):
        reminder_id = uuid4()
        client = MagicMock()
        with patch.object(push_service, "get_bool", return_value=True), patch.object(
            push_service, "_get_redis_client", return_value=client
        ), patch.object(push_service, "get", return_value="prefix:"), patch.object(
            push_service, "get_int", return_value=3600
        ):
            push_service._mark_dispatched(reminder_id)

        client.setex.assert_called_once_with(f"prefix:{reminder_id}", 3600, "1")


class TestSendPushNotification:
    @pytest.mark.asyncio
    async def test_skips_when_push_not_configured(self):
        with patch.object(push_service, "is_push_configured", return_value=False), patch.object(
            push_service, "send_fcm_notification", new_callable=AsyncMock
        ) as mock_send:
            await push_service.send_push_notification(_reminder(), "Title", "Body")

        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("platform", [PushPlatform.ANDROID, PushPlatform.IOS])
    async def test_sends_via_fcm_for_mobile_platforms(self, platform):
        reminder = _reminder(platform)
        with patch.object(push_service, "is_push_configured", return_value=True), patch.object(
            push_service, "send_fcm_notification", new_callable=AsyncMock
        ) as mock_send:
            await push_service.send_push_notification(
                reminder,
                "Title",
                "Body",
                "https://cdn.test/image.png",
            )

        mock_send.assert_awaited_once()
        kwargs = mock_send.await_args.kwargs
        assert kwargs["device_token"] == "device-token"
        assert kwargs["image_url"] == "https://cdn.test/image.png"
        assert kwargs["data"]["session_type"] == "PLAN"
        assert kwargs["data"]["source_id"] == str(reminder.plan_id)

    @pytest.mark.asyncio
    async def test_raises_for_unsupported_platform(self):
        reminder = _reminder("web")
        with patch.object(push_service, "is_push_configured", return_value=True), patch.object(
            push_service, "send_fcm_notification", new_callable=AsyncMock
        ):
            with pytest.raises(ValueError, match="Unsupported platform"):
                await push_service.send_push_notification(reminder, "Title", "Body")
