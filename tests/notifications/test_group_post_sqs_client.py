"""Tests for group post notification SQS client helpers."""
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

from botocore.exceptions import ClientError

from worker_api.notifications.group_post_sqs_client import (
    GROUP_POST_CREATED_EVENT,
    GROUP_POST_NOTIFICATION_EVENT_VERSION,
    delete_group_post_notification_message,
    get_group_post_notification_sqs_queue_url,
    is_group_post_notification_sqs_configured,
    is_group_post_notification_sqs_poll_enabled,
    parse_group_post_notification_message_body,
    receive_group_post_notification_messages,
)


class TestQueueConfig:
    @patch("worker_api.notifications.group_post_sqs_client.get", return_value="  https://sqs.example/group-post  ")
    def test_get_queue_url_strips(self, _get):
        assert get_group_post_notification_sqs_queue_url() == "https://sqs.example/group-post"

    @patch("worker_api.notifications.group_post_sqs_client.get_group_post_notification_sqs_queue_url", return_value="")
    def test_is_configured_false(self, _url):
        assert is_group_post_notification_sqs_configured() is False

    @patch("worker_api.notifications.group_post_sqs_client.is_group_post_notification_sqs_configured", return_value=True)
    @patch("worker_api.notifications.group_post_sqs_client.get_bool", return_value=True)
    def test_poll_enabled(self, _bool, _configured):
        assert is_group_post_notification_sqs_poll_enabled() is True


class TestParseBody:
    def test_valid_event(self):
        post_id = str(uuid4())
        body = parse_group_post_notification_message_body(
            json.dumps(
                {
                    "event_type": GROUP_POST_CREATED_EVENT,
                    "version": GROUP_POST_NOTIFICATION_EVENT_VERSION,
                    "post_id": post_id,
                }
            )
        )
        assert body["post_id"] == post_id

    def test_rejects_invalid_json(self):
        assert parse_group_post_notification_message_body("not-json") is None

    def test_rejects_wrong_event_type(self):
        assert parse_group_post_notification_message_body(
            json.dumps({"event_type": "OTHER", "version": 1, "post_id": str(uuid4())})
        ) is None

    def test_rejects_wrong_version(self):
        assert parse_group_post_notification_message_body(
            json.dumps(
                {
                    "event_type": GROUP_POST_CREATED_EVENT,
                    "version": 99,
                    "post_id": str(uuid4()),
                }
            )
        ) is None

    def test_rejects_missing_post_id(self):
        assert parse_group_post_notification_message_body(
            json.dumps({"event_type": GROUP_POST_CREATED_EVENT, "version": 1})
        ) is None


class TestReceiveAndDelete:
    @patch("worker_api.notifications.group_post_sqs_client.get_group_post_notification_sqs_queue_url", return_value="")
    def test_receive_empty_without_queue(self, _url):
        assert receive_group_post_notification_messages() == []

    @patch("worker_api.notifications.group_post_sqs_client.get_int", side_effect=lambda key: 1)
    @patch("worker_api.notifications.group_post_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.group_post_sqs_client.get_group_post_notification_sqs_queue_url",
        return_value="https://sqs.example/group-post",
    )
    def test_receive_messages(self, _url, mock_get_client, _get_int):
        client = MagicMock()
        client.receive_message.return_value = {"Messages": [{"MessageId": "1"}]}
        mock_get_client.return_value = client
        assert receive_group_post_notification_messages() == [{"MessageId": "1"}]

    @patch("worker_api.notifications.group_post_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.group_post_sqs_client.get_group_post_notification_sqs_queue_url",
        return_value="https://sqs.example/group-post",
    )
    def test_delete_message(self, _url, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        delete_group_post_notification_message("receipt")
        client.delete_message.assert_called_once()

    @patch("worker_api.notifications.group_post_sqs_client._get_sqs_client")
    @patch(
        "worker_api.notifications.group_post_sqs_client.get_group_post_notification_sqs_queue_url",
        return_value="https://sqs.example/group-post",
    )
    def test_delete_handles_client_error(self, _url, mock_get_client):
        client = MagicMock()
        client.delete_message.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "fail"}},
            "DeleteMessage",
        )
        mock_get_client.return_value = client
        delete_group_post_notification_message("receipt")
