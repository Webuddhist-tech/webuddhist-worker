import json
import logging
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from worker_api.config import get, get_bool, get_int

logger = logging.getLogger(__name__)

_sqs_client = None

PRAYER_TRANSLATION_REQUESTED_EVENT = "PRAYER_TRANSLATION_REQUESTED"
PRAYER_TRANSLATION_EVENT_VERSION = 1


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


def get_prayer_translation_sqs_queue_url() -> str:
    return get("PRAYER_TRANSLATION_SQS_QUEUE_URL").strip()


def is_prayer_translation_sqs_configured() -> bool:
    return bool(get_prayer_translation_sqs_queue_url())


def is_prayer_translation_sqs_poll_enabled() -> bool:
    return (
        get_bool("PRAYER_TRANSLATION_SQS_POLL_ENABLED")
        and is_prayer_translation_sqs_configured()
    )


def receive_prayer_translation_messages() -> List[Dict[str, Any]]:
    queue_url = get_prayer_translation_sqs_queue_url()
    if not queue_url:
        return []

    try:
        response = _get_sqs_client().receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=get_int("PRAYER_TRANSLATION_SQS_MAX_MESSAGES"),
            WaitTimeSeconds=get_int("PRAYER_TRANSLATION_SQS_WAIT_TIME_SECONDS"),
            VisibilityTimeout=get_int("PRAYER_TRANSLATION_SQS_VISIBILITY_TIMEOUT_SECONDS"),
            MessageAttributeNames=["All"],
        )
        return response.get("Messages", [])
    except ClientError as e:
        logger.error("Failed to receive prayer translation SQS messages: %s", e)
        return []


def delete_prayer_translation_message(receipt_handle: str) -> None:
    queue_url = get_prayer_translation_sqs_queue_url()
    if not queue_url or not receipt_handle:
        return

    try:
        _get_sqs_client().delete_message(
            QueueUrl=queue_url,
            ReceiptHandle=receipt_handle,
        )
    except ClientError as e:
        logger.error("Failed to delete prayer translation SQS message: %s", e)


def parse_prayer_translation_message_body(raw_body: str) -> Optional[Dict[str, Any]]:
    try:
        body = json.loads(raw_body)
    except (TypeError, json.JSONDecodeError) as e:
        logger.error("Invalid prayer translation SQS message body: %s", e)
        return None

    if not isinstance(body, dict) or not body.get("message_id"):
        logger.error("Prayer translation SQS message missing message_id: %s", body)
        return None
    return body
