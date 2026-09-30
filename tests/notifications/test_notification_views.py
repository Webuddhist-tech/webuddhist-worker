from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest


class TestDispatchEndpoint:
    def test_dispatch_requires_token(self, client):
        response = client.post("/api/v1/internal/dispatch-due-notifications")
        assert response.status_code == 422

    def test_dispatch_rejects_invalid_token(self, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        response = client.post(
            "/api/v1/internal/dispatch-due-notifications",
            headers={"X-Dispatch-Token": "wrong-token"},
        )
        assert response.status_code == 401

    @patch(
        "worker_api.notifications.internal_views.dispatch_due_notifications_service",
        new_callable=AsyncMock,
    )
    def test_dispatch_success(self, mock_dispatch, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        from worker_api.notifications.schemas import DispatchDueNotificationsResponse

        mock_dispatch.return_value = DispatchDueNotificationsResponse(
            processed=2,
            sent=2,
            failed=0,
            skipped=0,
        )

        response = client.post(
            "/api/v1/internal/dispatch-due-notifications",
            headers={"X-Dispatch-Token": "secret-token"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "processed": 2,
            "sent": 2,
            "failed": 0,
            "skipped": 0,
        }


class TestRoutineNotificationTargetsEndpoint:
    def test_routine_notification_targets_requires_token(self, client):
        response = client.get("/api/v1/internal/routine-notification-targets")
        assert response.status_code == 422

    def test_routine_notification_targets_rejects_invalid_token(self, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        response = client.get(
            "/api/v1/internal/routine-notification-targets",
            headers={"X-Dispatch-Token": "wrong-token"},
        )
        assert response.status_code == 401

    @patch(
        "worker_api.notifications.internal_views.get_routine_notification_targets",
        new_callable=AsyncMock,
    )
    def test_routine_notification_targets_success(self, mock_get_targets, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        from datetime import datetime, timezone
        from uuid import uuid4

        from worker_api.notifications.schemas import (
            NotificationContent,
            PushDeviceTarget,
            RoutineNotificationGroup,
            RoutineNotificationTargetsResponse,
            RoutineNotificationUserTarget,
        )

        user_id = uuid4()
        plan_id = uuid4()
        mock_get_targets.return_value = RoutineNotificationTargetsResponse(
            generated_at=datetime(2026, 6, 23, 10, 30, tzinfo=timezone.utc),
            matched_time_utc="10:30",
            groups=[
                RoutineNotificationGroup(
                    session_type="PLAN",
                    source_id=plan_id,
                    source_image_url="https://example.com/plan.png",
                    users=[
                        RoutineNotificationUserTarget(
                            user_id=user_id,
                                notification=NotificationContent(
                                    title="Day 1",
                                    body="Begin practice.",
                                    image_url="https://example.com/plan.png",
                                ),
                            push_devices=[
                                PushDeviceTarget(token="fcm-token", platform="android"),
                            ],
                        )
                    ],
                )
            ],
        )

        response = client.get(
            "/api/v1/internal/routine-notification-targets",
            headers={"X-Dispatch-Token": "secret-token"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["matched_time_utc"] == "10:30"
        assert payload["groups"][0]["session_type"] == "PLAN"
        assert payload["groups"][0]["users"][0]["push_devices"][0]["token"] == "fcm-token"
        mock_get_targets.assert_called_once()


class TestDispatchRoutineNotificationsEndpoint:
    def test_dispatch_routine_notifications_requires_token(self, client):
        response = client.post("/api/v1/internal/dispatch-routine-notifications")
        assert response.status_code == 422

    def test_dispatch_routine_notifications_rejects_invalid_token(self, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        response = client.post(
            "/api/v1/internal/dispatch-routine-notifications",
            headers={"X-Dispatch-Token": "wrong-token"},
        )
        assert response.status_code == 401

    @patch(
        "worker_api.notifications.internal_views.dispatch_routine_notifications_service",
        new_callable=AsyncMock,
    )
    def test_dispatch_routine_notifications_success(self, mock_dispatch, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        from datetime import datetime, timezone
        from uuid import uuid4

        from worker_api.notifications.schemas import (
            DispatchRoutineNotificationsResponse,
            NotificationContent,
            PushDeviceTarget,
            RoutineNotificationGroup,
            RoutineNotificationUserTarget,
        )

        user_id = uuid4()
        plan_id = uuid4()
        mock_dispatch.return_value = DispatchRoutineNotificationsResponse(
            generated_at=datetime(2026, 6, 23, 10, 30, tzinfo=timezone.utc),
            matched_time_utc="10:30",
            groups=[
                RoutineNotificationGroup(
                    session_type="PLAN",
                    source_id=plan_id,
                    source_image_url="https://example.com/plan.png",
                    users=[
                        RoutineNotificationUserTarget(
                            user_id=user_id,
                                notification=NotificationContent(
                                    title="Day 1",
                                    body="Begin practice.",
                                    image_url="https://example.com/plan.png",
                                ),
                            push_devices=[
                                PushDeviceTarget(token="fcm-token", platform="android"),
                            ],
                        )
                    ],
                )
            ],
            processed=1,
            sent=1,
            failed=0,
            skipped=0,
        )

        response = client.post(
            "/api/v1/internal/dispatch-routine-notifications",
            headers={"X-Dispatch-Token": "secret-token"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["sent"] == 1
        assert payload["processed"] == 1
        assert payload["groups"][0]["session_type"] == "PLAN"
        mock_dispatch.assert_called_once()


class TestDispatchVerseOfDayNotificationsEndpoint:
    def test_dispatch_verse_of_day_notifications_requires_token(self, client):
        response = client.post("/api/v1/internal/dispatch-verse-of-day-notifications")
        assert response.status_code == 422

    def test_dispatch_verse_of_day_notifications_rejects_invalid_token(self, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        response = client.post(
            "/api/v1/internal/dispatch-verse-of-day-notifications",
            headers={"X-Dispatch-Token": "wrong-token"},
        )
        assert response.status_code == 401

    @patch(
        "worker_api.notifications.internal_views.dispatch_verse_of_day_notifications_service",
        new_callable=AsyncMock,
    )
    def test_dispatch_verse_of_day_notifications_success(self, mock_dispatch, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        from datetime import datetime, timezone
        from uuid import uuid4

        from worker_api.notifications.schemas import (
            DispatchVerseOfDayNotificationsResponse,
            VerseOfDayNotificationContent,
            VerseOfDayNotificationUserTarget,
            VerseOfDayPushDeviceTarget,
        )

        user_id = uuid4()
        mock_dispatch.return_value = DispatchVerseOfDayNotificationsResponse(
            generated_at=datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc),
            users=[
                VerseOfDayNotificationUserTarget(
                    user_id=user_id,
                    notification=VerseOfDayNotificationContent(
                        title="WebBuddhist",
                        body="May all beings be happy.",
                    ),
                    push_devices=[
                        VerseOfDayPushDeviceTarget(token="fcm-token", platform="android"),
                    ],
                )
            ],
            processed=1,
            sent=1,
            failed=0,
            skipped=0,
        )

        response = client.post(
            "/api/v1/internal/dispatch-verse-of-day-notifications",
            headers={"X-Dispatch-Token": "secret-token"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["sent"] == 1
        assert payload["processed"] == 1
        assert payload["users"][0]["notification"]["body"] == "May all beings be happy."
        mock_dispatch.assert_called_once()


class TestSendTestNotificationEndpoint:
    def test_send_test_notification_requires_token(self, client):
        response = client.post(
            "/api/v1/internal/send-test-notification",
            json={
                "session_type": "PLAN",
                "device_token": "fcm-token",
            },
        )
        assert response.status_code == 422

    def test_send_test_notification_rejects_invalid_token(self, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        response = client.post(
            "/api/v1/internal/send-test-notification",
            headers={"X-Dispatch-Token": "wrong-token"},
            json={
                "session_type": "PLAN",
                "device_token": "fcm-token",
            },
        )
        assert response.status_code == 401

    def test_send_test_notification_requires_recipient(self, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        response = client.post(
            "/api/v1/internal/send-test-notification",
            headers={"X-Dispatch-Token": "secret-token"},
            json={"session_type": "PLAN"},
        )
        assert response.status_code == 422

    def test_send_test_notification_rejects_both_recipients(self, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        response = client.post(
            "/api/v1/internal/send-test-notification",
            headers={"X-Dispatch-Token": "secret-token"},
            json={
                "session_type": "PLAN",
                "device_token": "fcm-token",
                "email": "user@example.com",
            },
        )
        assert response.status_code == 422

    @patch(
        "worker_api.notifications.internal_views.send_test_notification_service",
        new_callable=AsyncMock,
    )
    def test_send_test_notification_success(self, mock_send, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        from uuid import uuid4

        from worker_api.notifications.schemas import (
            SendTestNotificationDelivery,
            SendTestNotificationResponse,
        )

        plan_id = uuid4()
        mock_send.return_value = SendTestNotificationResponse(
            title="Day 1",
            body="Begin practice.",
            session_type="PLAN",
            source_id=str(plan_id),
            sent=1,
            failed=0,
            deliveries=[
                SendTestNotificationDelivery(
                    device_token_prefix="fcm-token",
                    platform=None,
                    status="sent",
                )
            ],
        )

        response = client.post(
            "/api/v1/internal/send-test-notification",
            headers={"X-Dispatch-Token": "secret-token"},
            json={
                "title": "Day 1",
                "body": "Begin practice.",
                "session_type": "PLAN",
                "source_id": str(plan_id),
                "device_token": "fcm-token",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["sent"] == 1
        assert payload["session_type"] == "PLAN"
        assert payload["source_id"] == str(plan_id)
        mock_send.assert_called_once()

    @patch(
        "worker_api.notifications.internal_views.send_test_notification_service",
        new_callable=AsyncMock,
    )
    def test_send_test_notification_not_found(self, mock_send, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        mock_send.side_effect = ValueError("No active push devices found for email: user@example.com")

        response = client.post(
            "/api/v1/internal/send-test-notification",
            headers={"X-Dispatch-Token": "secret-token"},
            json={
                "session_type": "TIMER",
                "email": "user@example.com",
            },
        )

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "NOT_FOUND"

    @patch(
        "worker_api.notifications.internal_views.send_test_notification_service",
        new_callable=AsyncMock,
    )
    def test_send_test_notification_not_configured(self, mock_send, client, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_DISPATCH_SECRET_TOKEN", "secret-token")
        mock_send.side_effect = ValueError(
            "Firebase is not configured. Set GOOGLE_APPLICATION_CREDENTIALS to your "
            "Firebase service account JSON file path (or inline JSON)."
        )

        response = client.post(
            "/api/v1/internal/send-test-notification",
            headers={"X-Dispatch-Token": "secret-token"},
            json={
                "session_type": "PLAN",
                "device_token": "fcm-token",
            },
        )

        assert response.status_code == 503
        assert response.json()["detail"]["error"] == "NOT_CONFIGURED"


class TestReminderEndpoints:
    @patch("worker_api.notifications.reminder_views.enroll_reminder_service")
    def test_enroll_reminder(self, mock_enroll, client):
        reminder_id = uuid4()
        user_id = uuid4()
        plan_id = uuid4()

        mock_enroll.return_value = {
            "id": reminder_id,
            "user_id": user_id,
            "plan_id": plan_id,
            "trigger_at": "2026-06-22T09:00:00Z",
            "timezone": "UTC",
            "status": "pending",
            "platform": "android",
            "routine_config": {"times": ["09:00"]},
        }

        response = client.post(
            "/api/v1/notifications/reminders",
            json={
                "user_id": str(user_id),
                "plan_id": str(plan_id),
                "timezone": "UTC",
                "device_token": "fcm-token",
                "platform": "android",
                "routine": {"times": ["09:00"]},
            },
        )

        assert response.status_code == 201
        mock_enroll.assert_called_once()

    def test_enroll_invalid_time(self, client):
        response = client.post(
            "/api/v1/notifications/reminders",
            json={
                "user_id": str(uuid4()),
                "plan_id": str(uuid4()),
                "timezone": "UTC",
                "device_token": "fcm-token",
                "platform": "android",
                "routine": {"times": ["25:99"]},
            },
        )
        assert response.status_code == 422
