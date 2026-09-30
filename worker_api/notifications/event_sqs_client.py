import json
import logging
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from worker_api.config import get, get_bool, get_int

logger = logging.getLogger(__name__)

_sqs_client = None

EVENT_CREATED_EVENT = "EVENT_CREATED"
EVENT_REMINDER_EVENT = "EVENT_REMINDER"
EVENT_ANNOUNCEMENT_EVENT = "EVENT_ANNOUNCEMENT"
EVENT_NOTIFICATION_EVENT_VERSION = 1
EVENT_REMINDER_TYPES = {"T_MINUS_10", "T_ZERO"}
EVENT_ANNOUNCEMENT_AUDIENCES = {"participants", "group"}


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


def get_event_notification_sqs_queue_url() -> str:
    return get("EVENT_NOTIFICATION_SQS_QUEUE_URL").strip()


def is_event_notification_sqs_configured() -> bool:
    return bool(get_event_notification_sqs_queue_url())


def is_event_notification_sqs_poll_enabled() -> bool:
    return get_bool("EVENT_NOTIFICATION_SQS_POLL_ENABLED") and is_event_notification_sqs_configured()


def receive_event_notification_messages() -> List[Dict[str, Any]]:
    queue_url = get_event_notification_sqs_queue_url()
    if not queue_url:
        return []

    try:
        response = _get_sqs_client().receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=get_int("EVENT_NOTIFICATION_SQS_MAX_MESSAGES"),
            WaitTimeSeconds=get_int("EVENT_NOTIFICATION_SQS_WAIT_TIME_SECONDS"),
            VisibilityTimeout=get_int("EVENT_NOTIFICATION_SQS_VISIBILITY_TIMEOUT_SECONDS"),
            MessageAttributeNames=["All"],
        )
        return response.get("Messages", [])
    except ClientError as e:
        logger.error("Failed to receive event notification SQS messages: %s", e)
        return []


def delete_event_notification_message(receipt_handle: str) -> None:
    queue_url = get_event_notification_sqs_queue_url()
    if not queue_url or not receipt_handle:
        return

    try:
        _get_sqs_client().delete_message(
            QueueUrl=queue_url,
            ReceiptHandle=receipt_handle,
        )
    except ClientError as e:
        logger.error("Failed to delete event notification SQS message: %s", e)


def parse_event_notification_message_body(raw_body: str) -> Optional[Dict[str, Any]]:
    try:
        body = json.loads(raw_body)
    except (TypeError, json.JSONDecodeError) as e:
        logger.error("Invalid event notification SQS message body: %s", e)
        return None

    if not isinstance(body, dict):
        logger.error("Event notification SQS message body is not an object: %s", body)
        return None

    event_type = body.get("event_type")
    version = body.get("version")
    event_id = body.get("event_id")
    if event_type not in (
        EVENT_CREATED_EVENT,
        EVENT_REMINDER_EVENT,
        EVENT_ANNOUNCEMENT_EVENT,
    ):
        logger.error("Unsupported event notification event_type: %s", event_type)
        return None
    if version != EVENT_NOTIFICATION_EVENT_VERSION:
        logger.error("Unsupported event notification event version: %s", version)
        return None
    if not event_id:
        logger.error("Event notification SQS message missing event_id: %s", body)
        return None
    if event_type == EVENT_REMINDER_EVENT:
        reminder_type = body.get("reminder_type")
        if reminder_type not in EVENT_REMINDER_TYPES:
            logger.error("Unsupported event reminder reminder_type: %s", reminder_type)
            return None
    if event_type == EVENT_ANNOUNCEMENT_EVENT:
        # An announcement carries everything it needs to be rendered, so a
        # message missing any of it can never be delivered and is dropped
        # here rather than failing per device later.
        if body.get("audience") not in EVENT_ANNOUNCEMENT_AUDIENCES:
            logger.error(
                "Unsupported event announcement audience: %s", body.get("audience")
            )
            return None
        for field in ("announcement_id", "title", "body"):
            if not body.get(field):
                logger.error("Event announcement message missing %s: %s", field, body)
                return None
    return body
