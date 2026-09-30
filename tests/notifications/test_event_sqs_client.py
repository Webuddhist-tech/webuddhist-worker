"""Tests for event notification SQS client helpers."""
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

from botocore.exceptions import ClientError

from worker_api.notifications.event_sqs_client import (
    EVENT_CREATED_EVENT,
    EVENT_NOTIFICATION_EVENT_VERSION,
    EVENT_REMINDER_EVENT,
    delete_event_notification_message,
    get_event_notification_sqs_queue_url,
    is_event_notification_sqs_configured,
    is_event_notification_sqs_poll_enabled,
    parse_event_notification_message_body,
    receive_event_notification_messages,
)


class TestQueueConfig:
    @patch("worker_api.notifications.event_sqs_client.get", return_value="  https://sqs.example/event  ")
    def test_get_queue_url_strips(self, _get):
        assert get_event_notification_sqs_queue_url() == "https://sqs.example/event"

    @patch("worker_api.notifications.event_sqs_client.get_event_notification_sqs_queue_url", return_value="")
    def test_is_configured_false(self, _url):
        assert is_event_notification_sqs_configured() is False

    @patch("worker_api.notifications.event_sqs_client.is_event_notification_sqs_configured", return_value=True)
    @patch("worker_api.notifications.event_sqs_client.get_bool", return_value=True)
    def test_poll_enabled(self, _bool, _configured):
        assert is_event_notification_sqs_poll_enabled() is True


class TestParseBody:
    def test_valid_event(self):
        event_id = str(uuid4())
        body = parse_event_notification_message_body(
            json.dumps(
                {
                    "event_type": EVENT_CREATED_EVENT,
                    "version": EVENT_NOTIFICATION_EVENT_VERSION,
                    "event_id": event_id,
                }
            )
        )
        assert body["event_id"] == event_id

    def test_rejects_invalid_json(self):
        assert parse_event_notification_message_body("not-json") is None

    def test_rejects_wrong_event_type(self):
        assert parse_event_notification_message_body(
            json.dumps({"event_type": "OTHER", "version": 1, "event_id": str(uuid4())})
        ) is None

    def test_rejects_wrong_version(self):
        assert parse_event_notification_message_body(
            json.dumps(
                {
                    "event_type": EVENT_CREATED_EVENT,
                    "version": 99,
                    "event_id": str(uuid4()),
                }
            )
        ) is None

    def test_rejects_missing_event_id(self):
        assert parse_event_notification_message_body(
            json.dumps({"event_type": EVENT_CREATED_EVENT, "version": 1})
        ) is None

    def test_valid_reminder_t_minus_10(self):
        event_id = str(uuid4())
        body = parse_event_notification_message_body(
            json.dumps(
                {
                    "event_type": EVENT_REMINDER_EVENT,
                    "version": EVENT_NOTIFICATION_EVENT_VERSION,
                    "event_id": event_id,
                    "reminder_type": "T_MINUS_10",
                }
            )
        )
        assert body["event_id"] == event_id
        assert body["reminder_type"] == "T_MINUS_10"

    def test_valid_reminder_t_zero(self):
        event_id = str(uuid4())
        body = parse_event_notification_message_body(
            json.dumps(
                {
                    "event_type": EVENT_REMINDER_EVENT,
                    "version": EVENT_NOTIFICATION_EVENT_VERSION,
                    "event_id": event_id,
                    "reminder_type": "T_ZERO",
                }
            )
        )
        assert body["reminder_type"] == "T_ZERO"

    def test_rejects_reminder_missing_reminder_type(self):
        assert parse_event_notification_message_body(
            json.dumps(
                {
                    "event_type": EVENT_REMINDER_EVENT,
                    "version": EVENT_NOTIFICATION_EVENT_VERSION,
                    "event_id": str(uuid4()),
                }
            )
        ) is None

    def test_rejects_reminder_invalid_reminder_type(self):
        assert parse_event_notification_message_body(
            json.dumps(
                {
                    "event_type": EVENT_REMINDER_EVENT,
                    "version": EVENT_NOTIFICATION_EVENT_VERSION,
                    "event_id": str(uuid4()),
                    "reminder_type": "T_MINUS_60",
                }
            )
        ) is None


class TestReceiveAndDelete:
    @patch("worker_api.notifications.event_sqs_client.get_event_notification_sqs_queue_url", return_value="")
    def test_receive_empty_without_queue(self, _url):
        assert receive_event_notification_messages() == []

    @patch("worker_api.notifications.event_sqs_client.get_int", side_effect=lambda key: 1)
    @patch("worker_api.notifications.event_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.event_sqs_client.get_event_notification_sqs_queue_url",
        return_value="https://sqs.example/event",
    )
    def test_receive_messages(self, _url, mock_get_client, _get_int):
        client = MagicMock()
        client.receive_message.return_value = {"Messages": [{"MessageId": "1"}]}
        mock_get_client.return_value = client
        assert receive_event_notification_messages() == [{"MessageId": "1"}]

    @patch("worker_api.notifications.event_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.event_sqs_client.get_event_notification_sqs_queue_url",
        return_value="https://sqs.example/event",
    )
    def test_delete_message(self, _url, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        delete_event_notification_message("receipt")
        client.delete_message.assert_called_once()

    @patch("worker_api.notifications.event_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.event_sqs_client.get_event_notification_sqs_queue_url",
        return_value="https://sqs.example/event",
    )
    def test_delete_handles_client_error(self, _url, mock_get_client):
        client = MagicMock()
        client.delete_message.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "fail"}},
            "DeleteMessage",
        )
        mock_get_client.return_value = client
        delete_event_notification_message("receipt")
