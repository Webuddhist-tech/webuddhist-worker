"""Tests for chat notification SQS client helpers."""
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

from botocore.exceptions import ClientError

import worker_api.notifications.chat_sqs_client as sqs_module
from worker_api.notifications.chat_sqs_client import (
    CHAT_MESSAGE_CREATED_EVENT,
    CHAT_NOTIFICATION_EVENT_VERSION,
    PRAYER_RECEIVED_EVENT,
    delete_chat_notification_message,
    get_chat_notification_sqs_queue_url,
    is_chat_notification_sqs_configured,
    is_chat_notification_sqs_poll_enabled,
    parse_chat_notification_message_body,
    receive_chat_notification_messages,
)


class TestQueueConfig:
    @patch("worker_api.notifications.chat_sqs_client.get", return_value="  https://sqs.example/chat  ")
    def test_get_queue_url_strips(self, _get):
        assert get_chat_notification_sqs_queue_url() == "https://sqs.example/chat"

    @patch("worker_api.notifications.chat_sqs_client.get_chat_notification_sqs_queue_url", return_value="")
    def test_is_configured_false(self, _url):
        assert is_chat_notification_sqs_configured() is False

    @patch("worker_api.notifications.chat_sqs_client.is_chat_notification_sqs_configured", return_value=True)
    @patch("worker_api.notifications.chat_sqs_client.get_bool", return_value=True)
    def test_poll_enabled(self, _bool, _configured):
        assert is_chat_notification_sqs_poll_enabled() is True


class TestParseBody:
    def test_valid_event(self):
        message_id = str(uuid4())
        body = parse_chat_notification_message_body(
            json.dumps(
                {
                    "event_type": CHAT_MESSAGE_CREATED_EVENT,
                    "version": CHAT_NOTIFICATION_EVENT_VERSION,
                    "message_id": message_id,
                }
            )
        )
        assert body["message_id"] == message_id

    def test_valid_prayer_event(self):
        """Prayer notifications share the queue, keyed on prayer_id."""
        prayer_id = str(uuid4())
        body = parse_chat_notification_message_body(
            json.dumps(
                {
                    "event_type": PRAYER_RECEIVED_EVENT,
                    "version": CHAT_NOTIFICATION_EVENT_VERSION,
                    "prayer_id": prayer_id,
                }
            )
        )
        assert body["prayer_id"] == prayer_id

    def test_rejects_prayer_event_without_prayer_id(self):
        assert parse_chat_notification_message_body(
            json.dumps(
                {
                    "event_type": PRAYER_RECEIVED_EVENT,
                    "version": CHAT_NOTIFICATION_EVENT_VERSION,
                    "message_id": str(uuid4()),
                }
            )
        ) is None

    def test_rejects_invalid_json(self):
        assert parse_chat_notification_message_body("not-json") is None

    def test_rejects_wrong_event_type(self):
        assert parse_chat_notification_message_body(
            json.dumps({"event_type": "OTHER", "version": 1, "message_id": str(uuid4())})
        ) is None

    def test_rejects_wrong_version(self):
        assert parse_chat_notification_message_body(
            json.dumps(
                {
                    "event_type": CHAT_MESSAGE_CREATED_EVENT,
                    "version": 99,
                    "message_id": str(uuid4()),
                }
            )
        ) is None

    def test_rejects_missing_message_id(self):
        assert parse_chat_notification_message_body(
            json.dumps({"event_type": CHAT_MESSAGE_CREATED_EVENT, "version": 1})
        ) is None


class TestReceiveAndDelete:
    @patch("worker_api.notifications.chat_sqs_client.get_chat_notification_sqs_queue_url", return_value="")
    def test_receive_empty_without_queue(self, _url):
        assert receive_chat_notification_messages() == []

    @patch("worker_api.notifications.chat_sqs_client.get_int", side_effect=lambda key: 1)
    @patch("worker_api.notifications.chat_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.chat_sqs_client.get_chat_notification_sqs_queue_url",
        return_value="https://sqs.example/chat",
    )
    def test_receive_messages(self, _url, mock_get_client, _get_int):
        client = MagicMock()
        client.receive_message.return_value = {"Messages": [{"MessageId": "1"}]}
        mock_get_client.return_value = client
        assert receive_chat_notification_messages() == [{"MessageId": "1"}]

    @patch("worker_api.notifications.chat_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.chat_sqs_client.get_chat_notification_sqs_queue_url",
        return_value="https://sqs.example/chat",
    )
    def test_delete_message(self, _url, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        delete_chat_notification_message("receipt")
        client.delete_message.assert_called_once()

    @patch("worker_api.notifications.chat_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.chat_sqs_client.get_chat_notification_sqs_queue_url",
        return_value="https://sqs.example/chat",
    )
    def test_delete_handles_client_error(self, _url, mock_get_client):
        client = MagicMock()
        client.delete_message.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "fail"}},
            "DeleteMessage",
        )
        mock_get_client.return_value = client
        delete_chat_notification_message("receipt")
