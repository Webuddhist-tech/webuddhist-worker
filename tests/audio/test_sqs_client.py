"""Tests for audio SQS client helpers."""
import json
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from worker_api.audio.sqs_client import (
    _get_sqs_client,
    delete_audio_job_message,
    get_audio_sqs_queue_url,
    is_audio_sqs_configured,
    is_audio_sqs_poll_enabled,
    parse_audio_job_message_body,
    receive_audio_job_messages,
)
import worker_api.audio.sqs_client as sqs_module


class TestQueueConfig:
    @patch("worker_api.audio.sqs_client.get", return_value="  https://sqs.example/queue  ")
    def test_get_queue_url_strips(self, _get):
        assert get_audio_sqs_queue_url() == "https://sqs.example/queue"

    @patch("worker_api.audio.sqs_client.get_audio_sqs_queue_url", return_value="https://sqs.example/queue")
    def test_is_configured_true(self, _url):
        assert is_audio_sqs_configured() is True

    @patch("worker_api.audio.sqs_client.get_audio_sqs_queue_url", return_value="")
    def test_is_configured_false(self, _url):
        assert is_audio_sqs_configured() is False

    @patch("worker_api.audio.sqs_client.is_audio_sqs_configured", return_value=True)
    @patch("worker_api.audio.sqs_client.get_bool", return_value=True)
    def test_poll_enabled(self, _bool, _configured):
        assert is_audio_sqs_poll_enabled() is True

    @patch("worker_api.audio.sqs_client.is_audio_sqs_configured", return_value=False)
    @patch("worker_api.audio.sqs_client.get_bool", return_value=True)
    def test_poll_disabled_when_unconfigured(self, _bool, _configured):
        assert is_audio_sqs_poll_enabled() is False


class TestGetSqsClient:
    def setup_method(self):
        sqs_module._sqs_client = None

    def teardown_method(self):
        sqs_module._sqs_client = None

    @patch("worker_api.audio.sqs_client.boto3.client")
    @patch("worker_api.audio.sqs_client.get", side_effect=lambda key: f"value-{key}")
    def test_creates_client_once(self, _get, mock_boto_client):
        mock_boto_client.return_value = MagicMock(name="sqs")

        first = _get_sqs_client()
        second = _get_sqs_client()

        assert first is second
        mock_boto_client.assert_called_once_with(
            "sqs",
            aws_access_key_id="value-AWS_ACCESS_KEY",
            aws_secret_access_key="value-AWS_SECRET_KEY",
            region_name="value-AWS_REGION",
        )


class TestReceiveMessages:
    @patch("worker_api.audio.sqs_client.get_audio_sqs_queue_url", return_value="")
    def test_returns_empty_without_queue(self, _url):
        assert receive_audio_job_messages() == []

    @patch("worker_api.audio.sqs_client.get_int", side_effect=lambda key: 1)
    @patch("worker_api.audio.sqs_client._get_sqs_client")
    @patch("worker_api.audio.sqs_client.get_audio_sqs_queue_url", return_value="https://sqs.example/queue")
    def test_returns_messages(self, _url, mock_get_client, _get_int):
        client = MagicMock()
        client.receive_message.return_value = {"Messages": [{"MessageId": "1"}]}
        mock_get_client.return_value = client

        assert receive_audio_job_messages() == [{"MessageId": "1"}]

    @patch("worker_api.audio.sqs_client.get_int", side_effect=lambda key: 1)
    @patch("worker_api.audio.sqs_client._get_sqs_client")
    @patch("worker_api.audio.sqs_client.get_audio_sqs_queue_url", return_value="https://sqs.example/queue")
    def test_returns_empty_on_client_error(self, _url, mock_get_client, _get_int):
        client = MagicMock()
        client.receive_message.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}},
            "ReceiveMessage",
        )
        mock_get_client.return_value = client

        assert receive_audio_job_messages() == []


class TestDeleteMessage:
    @patch("worker_api.audio.sqs_client.get_audio_sqs_queue_url", return_value="")
    def test_noop_without_queue(self, _url):
        delete_audio_job_message("receipt")

    @patch("worker_api.audio.sqs_client.get_audio_sqs_queue_url", return_value="https://sqs.example/queue")
    def test_noop_without_receipt(self, _url):
        delete_audio_job_message("")

    @patch("worker_api.audio.sqs_client._get_sqs_client")
    @patch("worker_api.audio.sqs_client.get_audio_sqs_queue_url", return_value="https://sqs.example/queue")
    def test_deletes_message(self, _url, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client

        delete_audio_job_message("receipt-1")

        client.delete_message.assert_called_once_with(
            QueueUrl="https://sqs.example/queue",
            ReceiptHandle="receipt-1",
        )

    @patch("worker_api.audio.sqs_client._get_sqs_client")
    @patch("worker_api.audio.sqs_client.get_audio_sqs_queue_url", return_value="https://sqs.example/queue")
    def test_swallows_client_error(self, _url, mock_get_client):
        client = MagicMock()
        client.delete_message.side_effect = ClientError(
            {"Error": {"Code": "ReceiptHandleIsInvalid", "Message": "bad"}},
            "DeleteMessage",
        )
        mock_get_client.return_value = client

        delete_audio_job_message("receipt-1")


class TestParseMessageBody:
    def test_parses_valid_body(self):
        body = parse_audio_job_message_body(json.dumps({"job_id": "abc", "language": "en"}))
        assert body["job_id"] == "abc"

    def test_invalid_json(self):
        assert parse_audio_job_message_body("not-json") is None

    def test_missing_job_id(self):
        assert parse_audio_job_message_body(json.dumps({"language": "en"})) is None

    def test_non_dict_body(self):
        assert parse_audio_job_message_body(json.dumps(["job"])) is None
