import json
import logging
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from worker_api.config import get, get_bool, get_int

logger = logging.getLogger(__name__)

_sqs_client = None

GROUP_POST_CREATED_EVENT = "GROUP_POST_CREATED"
GROUP_POST_NOTIFICATION_EVENT_VERSION = 1


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


def get_group_post_notification_sqs_queue_url() -> str:
    return get("GROUP_POST_NOTIFICATION_SQS_QUEUE_URL").strip()


def is_group_post_notification_sqs_configured() -> bool:
    return bool(get_group_post_notification_sqs_queue_url())


def is_group_post_notification_sqs_poll_enabled() -> bool:
    return get_bool("GROUP_POST_NOTIFICATION_SQS_POLL_ENABLED") and is_group_post_notification_sqs_configured()


def receive_group_post_notification_messages() -> List[Dict[str, Any]]:
    queue_url = get_group_post_notification_sqs_queue_url()
    if not queue_url:
        return []

    try:
        response = _get_sqs_client().receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=get_int("GROUP_POST_NOTIFICATION_SQS_MAX_MESSAGES"),
            WaitTimeSeconds=get_int("GROUP_POST_NOTIFICATION_SQS_WAIT_TIME_SECONDS"),
            VisibilityTimeout=get_int("GROUP_POST_NOTIFICATION_SQS_VISIBILITY_TIMEOUT_SECONDS"),
            MessageAttributeNames=["All"],
        )
        return response.get("Messages", [])
    except ClientError as e:
        logger.error("Failed to receive group post notification SQS messages: %s", e)
        return []


def delete_group_post_notification_message(receipt_handle: str) -> None:
    queue_url = get_group_post_notification_sqs_queue_url()
    if not queue_url or not receipt_handle:
        return

    try:
        _get_sqs_client().delete_message(
            QueueUrl=queue_url,
            ReceiptHandle=receipt_handle,
        )
    except ClientError as e:
        logger.error("Failed to delete group post notification SQS message: %s", e)


def parse_group_post_notification_message_body(raw_body: str) -> Optional[Dict[str, Any]]:
    try:
        body = json.loads(raw_body)
    except (TypeError, json.JSONDecodeError) as e:
        logger.error("Invalid group post notification SQS message body: %s", e)
        return None

    if not isinstance(body, dict):
        logger.error("Group post notification SQS message body is not an object: %s", body)
        return None

    event_type = body.get("event_type")
    version = body.get("version")
    post_id = body.get("post_id")
    if event_type != GROUP_POST_CREATED_EVENT:
        logger.error("Unsupported group post notification event_type: %s", event_type)
        return None
    if version != GROUP_POST_NOTIFICATION_EVENT_VERSION:
        logger.error("Unsupported group post notification event version: %s", version)
        return None
    if not post_id:
        logger.error("Group post notification SQS message missing post_id: %s", body)
        return None
    return body
