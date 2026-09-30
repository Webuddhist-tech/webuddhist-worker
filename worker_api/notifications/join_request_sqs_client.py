import json
import logging
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from worker_api.config import get, get_bool, get_int

logger = logging.getLogger(__name__)

_sqs_client = None

JOIN_REQUEST_CREATED_EVENT = "JOIN_REQUEST_CREATED"
JOIN_REQUEST_DECIDED_EVENT = "JOIN_REQUEST_DECIDED"
JOIN_REQUEST_NOTIFICATION_EVENTS = frozenset(
    {JOIN_REQUEST_CREATED_EVENT, JOIN_REQUEST_DECIDED_EVENT}
)
JOIN_REQUEST_NOTIFICATION_EVENT_VERSION = 1


def _get_sqs_client():
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client(
            "sqs",
            aws_access_key_id=get("AWS_ACCESS_KEY"),
            aws_secret_access_key=get("AWS_SECRET_KEY"),
            region_name=get("AWS_REGION"),
        )
    return _sqs_client


def get_join_request_notification_sqs_queue_url() -> str:
    return get("JOIN_REQUEST_NOTIFICATION_SQS_QUEUE_URL").strip()


def is_join_request_notification_sqs_configured() -> bool:
    return bool(get_join_request_notification_sqs_queue_url())


def is_join_request_notification_sqs_poll_enabled() -> bool:
    return (
        get_bool("JOIN_REQUEST_NOTIFICATION_SQS_POLL_ENABLED")
        and is_join_request_notification_sqs_configured()
    )


def receive_join_request_notification_messages() -> List[Dict[str, Any]]:
    queue_url = get_join_request_notification_sqs_queue_url()
    if not queue_url:
        return []

    try:
        response = _get_sqs_client().receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=get_int("JOIN_REQUEST_NOTIFICATION_SQS_MAX_MESSAGES"),
            WaitTimeSeconds=get_int("JOIN_REQUEST_NOTIFICATION_SQS_WAIT_TIME_SECONDS"),
            VisibilityTimeout=get_int("JOIN_REQUEST_NOTIFICATION_SQS_VISIBILITY_TIMEOUT_SECONDS"),
            MessageAttributeNames=["All"],
        )
        return response.get("Messages", [])
    except ClientError as e:
        logger.error("Failed to receive join request notification SQS messages: %s", e)
        return []


def delete_join_request_notification_message(receipt_handle: str) -> None:
    queue_url = get_join_request_notification_sqs_queue_url()
    if not queue_url or not receipt_handle:
        return

    try:
        _get_sqs_client().delete_message(
            QueueUrl=queue_url,
            ReceiptHandle=receipt_handle,
        )
    except ClientError as e:
        logger.error("Failed to delete join request notification SQS message: %s", e)


def parse_join_request_notification_message_body(raw_body: str) -> Optional[Dict[str, Any]]:
    try:
        body = json.loads(raw_body)
    except (TypeError, json.JSONDecodeError) as e:
        logger.error("Invalid join request notification SQS message body: %s", e)
        return None

    if not isinstance(body, dict):
        logger.error("Join request notification SQS message body is not an object: %s", body)
        return None

    event_type = body.get("event_type")
    version = body.get("version")
    join_request_id = body.get("join_request_id")
    if event_type not in JOIN_REQUEST_NOTIFICATION_EVENTS:
        logger.error("Unsupported join request notification event_type: %s", event_type)
        return None
    if version != JOIN_REQUEST_NOTIFICATION_EVENT_VERSION:
        logger.error("Unsupported join request notification event version: %s", version)
        return None
    if not join_request_id:
        logger.error(
            "Join request notification SQS message missing join_request_id: %s", body
        )
        return None
    return body
