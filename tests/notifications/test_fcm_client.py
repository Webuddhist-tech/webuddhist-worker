from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from worker_api.notifications.services.push.fcm_client import (
    build_chat_notification_data,
    build_event_notification_data,
    build_event_reminder_notification_data,
    build_group_post_notification_data,
    build_routine_notification_data,
    send_chat_push_notification,
    send_event_push_notification,
    send_event_reminder_push_notification,
    send_fcm_notification,
    send_group_post_push_notification,
    send_routine_push_notification,
)


class TestBuildRoutineNotificationData:
    def test_includes_session_metadata_and_content(self):
        source_id = uuid4()
        data = build_routine_notification_data(
            session_type="PLAN",
            source_id=source_id,
            title="Day 1",
            body="Begin practice.",
            image_url="https://example.com/plan.png",
        )
        assert data == {
            "session_type": "PLAN",
            "source_id": str(source_id),
            "title": "Day 1",
            "body": "Begin practice.",
            "image_url": "https://example.com/plan.png",
        }

    def test_empty_optional_fields_when_missing(self):
        data = build_routine_notification_data(
            session_type="SERIES",
            source_id=None,
            title="Series title",
            body="Default body",
        )
        assert data == {
            "session_type": "SERIES",
            "source_id": "",
            "title": "Series title",
            "body": "Default body",
            "image_url": "",
        }


class TestBuildChatNotificationData:
    def test_includes_chat_routing_fields(self):
        room_id = uuid4()
        message_id = uuid4()
        sender_id = uuid4()
        group_id = uuid4()
        data = build_chat_notification_data(
            room_id=room_id,
            message_id=message_id,
            sender_id=sender_id,
            chat_kind="GROUP",
            group_id=group_id,
            title="Sangha",
            body="Alice: Hello",
        )
        assert data == {
            "notification_type": "CHAT_MESSAGE",
            "session_type": "CHAT",
            "chat_kind": "GROUP",
            "message_type": "TEXT",
            "room_id": str(room_id),
            "message_id": str(message_id),
            "sender_id": str(sender_id),
            "group_id": str(group_id),
            "source_id": str(room_id),
            "title": "Sangha",
            "body": "Alice: Hello",
            "image_url": "",
        }

    def test_prayer_request_gets_own_type_and_image(self):
        room_id = uuid4()
        data = build_chat_notification_data(
            room_id=room_id,
            message_id=uuid4(),
            sender_id=uuid4(),
            chat_kind="EVENT",
            group_id=uuid4(),
            title="Tenzin Youdon is requesting a prayer 🙏",
            body="For my niece Sarah.",
            message_type="PRAYER",
            image_url="https://example.com/event.png",
        )
        assert data["notification_type"] == "PRAYER_REQUEST"
        assert data["message_type"] == "PRAYER"
        assert data["image_url"] == "https://example.com/event.png"
        # Still routes into the room it came from.
        assert data["session_type"] == "CHAT"
        assert data["source_id"] == str(room_id)

    def test_empty_group_id_for_private(self):
        data = build_chat_notification_data(
            room_id=uuid4(),
            message_id=uuid4(),
            sender_id=uuid4(),
            chat_kind="PRIVATE",
            group_id=None,
            title="Alice",
            body="Hi",
        )
        assert data["chat_kind"] == "PRIVATE"
        assert data["group_id"] == ""


class TestBuildGroupPostNotificationData:
    def test_includes_group_post_routing_fields(self):
        post_id = uuid4()
        group_id = uuid4()
        author_id = uuid4()
        data = build_group_post_notification_data(
            post_id=post_id,
            group_id=group_id,
            author_id=author_id,
            title="Sangha",
            body="Alice shared a new post",
        )
        assert data == {
            "notification_type": "GROUP_POST",
            "session_type": "GROUP_POST",
            "post_id": str(post_id),
            "group_id": str(group_id),
            "author_id": str(author_id),
            "source_id": str(post_id),
            "title": "Sangha",
            "body": "Alice shared a new post",
            "image_url": "",
        }


class TestBuildEventNotificationData:
    def test_includes_event_routing_fields(self):
        event_id = uuid4()
        group_id = uuid4()
        author_id = uuid4()
        data = build_event_notification_data(
            event_id=event_id,
            group_id=group_id,
            author_id=author_id,
            title="Sangha",
            body="Full Moon Meditation",
        )
        assert data == {
            "notification_type": "EVENT",
            "session_type": "EVENT",
            "event_id": str(event_id),
            "group_id": str(group_id),
            "author_id": str(author_id),
            "source_id": str(event_id),
            "title": "Sangha",
            "body": "Full Moon Meditation",
            "image_url": "",
        }


class TestBuildEventReminderNotificationData:
    def test_includes_reminder_routing_fields(self):
        event_id = uuid4()
        data = build_event_reminder_notification_data(
            event_id=event_id,
            reminder_type="T_MINUS_10",
            title="Sangha",
            body="Starting in 10 minutes",
        )
        assert data == {
            "notification_type": "EVENT_REMINDER",
            "session_type": "EVENT_REMINDER",
            "reminder_type": "T_MINUS_10",
            "event_id": str(event_id),
            "source_id": str(event_id),
            "title": "Sangha",
            "body": "Starting in 10 minutes",
            "image_url": "",
        }


class TestSendFcmNotification:
    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.push.fcm_client.messaging.send")
    @patch("worker_api.notifications.services.push.fcm_client.initialize_firebase")
    async def test_passes_notification_image_and_data_payload(
        self,
        mock_initialize_firebase,
        mock_send,
    ):
        source_id = uuid4()
        mock_send.return_value = "message-id"

        await send_fcm_notification(
            device_token="device-token",
            title="Title",
            body="Body",
            image_url="https://example.com/image.png",
            data=build_routine_notification_data(
                session_type="SERIES",
                source_id=source_id,
                title="Title",
                body="Body",
                image_url="https://example.com/image.png",
            ),
        )

        mock_initialize_firebase.assert_called_once()
        mock_send.assert_called_once()
        message = mock_send.call_args.args[0]
        assert message.notification.title == "Title"
        assert message.notification.body == "Body"
        assert message.notification.image == "https://example.com/image.png"
        assert message.token == "device-token"
        assert message.data == {
            "session_type": "SERIES",
            "source_id": str(source_id),
            "title": "Title",
            "body": "Body",
            "image_url": "https://example.com/image.png",
        }


class TestSendRoutinePushNotification:
    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.push.fcm_client.send_fcm_notification")
    async def test_delegates_to_send_fcm_notification(self, mock_send):
        source_id = uuid4()
        await send_routine_push_notification(
            device_token="device-token",
            session_type="PLAN",
            source_id=source_id,
            title="Title",
            body="Body",
            image_url="https://example.com/image.png",
        )

        mock_send.assert_awaited_once_with(
            device_token="device-token",
            title="Title",
            body="Body",
            image_url="https://example.com/image.png",
            data={
                "session_type": "PLAN",
                "source_id": str(source_id),
                "title": "Title",
                "body": "Body",
                "image_url": "https://example.com/image.png",
            },
        )


class TestSendChatPushNotification:
    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.push.fcm_client.send_fcm_notification")
    async def test_delegates_with_chat_payload(self, mock_send):
        room_id = uuid4()
        message_id = uuid4()
        sender_id = uuid4()
        await send_chat_push_notification(
            device_token="device-token",
            room_id=room_id,
            message_id=message_id,
            sender_id=sender_id,
            chat_kind="PRIVATE",
            group_id=None,
            title="Alice",
            body="Hi",
        )
        mock_send.assert_awaited_once()
        kwargs = mock_send.await_args.kwargs
        assert kwargs["title"] == "Alice"
        assert kwargs["body"] == "Hi"
        assert kwargs["data"]["notification_type"] == "CHAT_MESSAGE"
        assert kwargs["data"]["session_type"] == "CHAT"
        assert kwargs["data"]["room_id"] == str(room_id)


class TestSendGroupPostPushNotification:
    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.push.fcm_client.send_fcm_notification")
    async def test_delegates_with_group_post_payload(self, mock_send):
        post_id = uuid4()
        group_id = uuid4()
        author_id = uuid4()
        await send_group_post_push_notification(
            device_token="device-token",
            post_id=post_id,
            group_id=group_id,
            author_id=author_id,
            title="Sangha",
            body="Alice shared a new post",
        )
        mock_send.assert_awaited_once()
        kwargs = mock_send.await_args.kwargs
        assert kwargs["title"] == "Sangha"
        assert kwargs["body"] == "Alice shared a new post"
        assert kwargs["data"]["notification_type"] == "GROUP_POST"
        assert kwargs["data"]["session_type"] == "GROUP_POST"
        assert kwargs["data"]["post_id"] == str(post_id)


class TestSendEventPushNotification:
    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.push.fcm_client.send_fcm_notification")
    async def test_delegates_with_event_payload(self, mock_send):
        event_id = uuid4()
        group_id = uuid4()
        author_id = uuid4()
        await send_event_push_notification(
            device_token="device-token",
            event_id=event_id,
            group_id=group_id,
            author_id=author_id,
            title="Sangha",
            body="Full Moon Meditation",
        )
        mock_send.assert_awaited_once()
        kwargs = mock_send.await_args.kwargs
        assert kwargs["title"] == "Sangha"
        assert kwargs["body"] == "Full Moon Meditation"
        assert kwargs["data"]["notification_type"] == "EVENT"
        assert kwargs["data"]["session_type"] == "EVENT"
        assert kwargs["data"]["event_id"] == str(event_id)


class TestSendEventReminderPushNotification:
    @pytest.mark.asyncio
    @patch("worker_api.notifications.services.push.fcm_client.send_fcm_notification")
    async def test_delegates_with_reminder_payload(self, mock_send):
        event_id = uuid4()
        await send_event_reminder_push_notification(
            device_token="device-token",
            event_id=event_id,
            reminder_type="T_ZERO",
            title="Sangha",
            body="Starting now",
        )
        mock_send.assert_awaited_once()
        kwargs = mock_send.await_args.kwargs
        assert kwargs["title"] == "Sangha"
        assert kwargs["body"] == "Starting now"
        assert kwargs["data"]["notification_type"] == "EVENT_REMINDER"
        assert kwargs["data"]["reminder_type"] == "T_ZERO"
        assert kwargs["data"]["event_id"] == str(event_id)
