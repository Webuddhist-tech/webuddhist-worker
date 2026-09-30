"""Tests for join request notification SQS client helpers."""
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

from botocore.exceptions import ClientError

from worker_api.notifications.join_request_sqs_client import (
    JOIN_REQUEST_CREATED_EVENT,
    JOIN_REQUEST_DECIDED_EVENT,
    JOIN_REQUEST_NOTIFICATION_EVENT_VERSION,
    delete_join_request_notification_message,
    get_join_request_notification_sqs_queue_url,
    is_join_request_notification_sqs_configured,
    is_join_request_notification_sqs_poll_enabled,
    parse_join_request_notification_message_body,
    receive_join_request_notification_messages,
)


class TestQueueConfig:
    @patch(
        "worker_api.notifications.join_request_sqs_client.get",
        return_value="  https://sqs.example/join-request  ",
    )
    def test_get_queue_url_strips(self, _get):
        assert get_join_request_notification_sqs_queue_url() == "https://sqs.example/join-request"

    @patch(
        "worker_api.notifications.join_request_sqs_client.get_join_request_notification_sqs_queue_url",
        return_value="",
    )
    def test_is_configured_false(self, _url):
        assert is_join_request_notification_sqs_configured() is False

    @patch(
        "worker_api.notifications.join_request_sqs_client.is_join_request_notification_sqs_configured",
        return_value=True,
    )
    @patch("worker_api.notifications.join_request_sqs_client.get_bool", return_value=True)
    def test_poll_enabled(self, _bool, _configured):
        assert is_join_request_notification_sqs_poll_enabled() is True

    @patch(
        "worker_api.notifications.join_request_sqs_client.is_join_request_notification_sqs_configured",
        return_value=False,
    )
    @patch("worker_api.notifications.join_request_sqs_client.get_bool", return_value=True)
    def test_poll_disabled_without_queue(self, _bool, _configured):
        assert is_join_request_notification_sqs_poll_enabled() is False


class TestParseBody:
    def test_accepts_created_event(self):
        join_request_id = str(uuid4())
        body = parse_join_request_notification_message_body(
            json.dumps(
                {
                    "event_type": JOIN_REQUEST_CREATED_EVENT,
                    "version": JOIN_REQUEST_NOTIFICATION_EVENT_VERSION,
                    "join_request_id": join_request_id,
                }
            )
        )
        assert body["join_request_id"] == join_request_id
        assert body["event_type"] == JOIN_REQUEST_CREATED_EVENT

    def test_accepts_decided_event(self):
        join_request_id = str(uuid4())
        body = parse_join_request_notification_message_body(
            json.dumps(
                {
                    "event_type": JOIN_REQUEST_DECIDED_EVENT,
                    "version": JOIN_REQUEST_NOTIFICATION_EVENT_VERSION,
                    "join_request_id": join_request_id,
                }
            )
        )
        assert body["join_request_id"] == join_request_id
        assert body["event_type"] == JOIN_REQUEST_DECIDED_EVENT

    def test_rejects_invalid_json(self):
        assert parse_join_request_notification_message_body("not-json") is None

    def test_rejects_non_object_body(self):
        assert parse_join_request_notification_message_body(json.dumps([1, 2, 3])) is None

    def test_rejects_unknown_event_type(self):
        assert parse_join_request_notification_message_body(
            json.dumps(
                {
                    "event_type": "CHAT_MESSAGE_CREATED",
                    "version": 1,
                    "join_request_id": str(uuid4()),
                }
            )
        ) is None

    def test_rejects_wrong_version(self):
        assert parse_join_request_notification_message_body(
            json.dumps(
                {
                    "event_type": JOIN_REQUEST_CREATED_EVENT,
                    "version": 99,
                    "join_request_id": str(uuid4()),
                }
            )
        ) is None

    def test_rejects_missing_join_request_id(self):
        assert parse_join_request_notification_message_body(
            json.dumps(
                {
                    "event_type": JOIN_REQUEST_DECIDED_EVENT,
                    "version": 1,
                }
            )
        ) is None


class TestReceiveAndDelete:
    @patch(
        "worker_api.notifications.join_request_sqs_client.get_join_request_notification_sqs_queue_url",
        return_value="",
    )
    def test_receive_empty_without_queue(self, _url):
        assert receive_join_request_notification_messages() == []

    @patch("worker_api.notifications.join_request_sqs_client.get_int", side_effect=lambda key: 1)
    @patch("worker_api.notifications.join_request_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.join_request_sqs_client.get_join_request_notification_sqs_queue_url",
        return_value="https://sqs.example/join-request",
    )
    def test_receive_messages(self, _url, mock_get_client, _get_int):
        client = MagicMock()
        client.receive_message.return_value = {"Messages": [{"MessageId": "1"}]}
        mock_get_client.return_value = client
        assert receive_join_request_notification_messages() == [{"MessageId": "1"}]

    @patch("worker_api.notifications.join_request_sqs_client.get_int", side_effect=lambda key: 1)
    @patch("worker_api.notifications.join_request_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.join_request_sqs_client.get_join_request_notification_sqs_queue_url",
        return_value="https://sqs.example/join-request",
    )
    def test_receive_handles_client_error(self, _url, mock_get_client, _get_int):
        client = MagicMock()
        client.receive_message.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "fail"}},
            "ReceiveMessage",
        )
        mock_get_client.return_value = client
        assert receive_join_request_notification_messages() == []

    @patch("worker_api.notifications.join_request_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.join_request_sqs_client.get_join_request_notification_sqs_queue_url",
        return_value="https://sqs.example/join-request",
    )
    def test_delete_message(self, _url, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        delete_join_request_notification_message("receipt")
        client.delete_message.assert_called_once()

    @patch("worker_api.notifications.join_request_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.join_request_sqs_client.get_join_request_notification_sqs_queue_url",
        return_value="https://sqs.example/join-request",
    )
    def test_delete_handles_client_error(self, _url, mock_get_client):
        client = MagicMock()
        client.delete_message.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "fail"}},
            "DeleteMessage",
        )
        mock_get_client.return_value = client
        delete_join_request_notification_message("receipt")
