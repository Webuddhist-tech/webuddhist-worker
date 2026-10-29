import json
import logging
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from worker_api.config import get, get_bool, get_int
from worker_api.recommendations.schemas import RecommendationEvent

logger = logging.getLogger(__name__)

_sqs_client = None

RECOMMENDATION_EVENTS_EVENT_TYPE = "RECOMMENDATION_EVENTS"
RECOMMENDATION_EVENTS_VERSION = 1


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


def get_recommendation_events_sqs_queue_url() -> str:
    return get("RECOMMENDATION_EVENTS_SQS_QUEUE_URL").strip()


def is_recommendation_events_sqs_poll_enabled() -> bool:
    return get_bool("RECOMMENDATION_EVENTS_SQS_POLL_ENABLED") and bool(
        get_recommendation_events_sqs_queue_url()
    )


def receive_recommendation_events_messages() -> List[Dict[str, Any]]:
    queue_url = get_recommendation_events_sqs_queue_url()
    if not queue_url:
        return []

    try:
        response = _get_sqs_client().receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=get_int("RECOMMENDATION_EVENTS_SQS_MAX_MESSAGES"),
            WaitTimeSeconds=get_int("RECOMMENDATION_EVENTS_SQS_WAIT_TIME_SECONDS"),
            VisibilityTimeout=get_int("RECOMMENDATION_EVENTS_SQS_VISIBILITY_TIMEOUT_SECONDS"),
        )
        return response.get("Messages", [])
    except ClientError as e:
        logger.error("Failed to receive recommendation events SQS messages: %s", e)
        return []


def delete_recommendation_events_message(receipt_handle: str) -> None:
    queue_url = get_recommendation_events_sqs_queue_url()
    if not queue_url or not receipt_handle:
        return

    try:
        _get_sqs_client().delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
    except ClientError as e:
        logger.error("Failed to delete recommendation events SQS message: %s", e)


def parse_recommendation_events_message_body(
    raw_body: str,
) -> Optional[List[RecommendationEvent]]:
    """The message's events, or None when the message is not one we read.

    One malformed event is skipped and logged rather than dropping the
    whole batch it came in."""
    try:
        body = json.loads(raw_body)
    except (TypeError, json.JSONDecodeError) as e:
        logger.error("Invalid recommendation events SQS message body: %s", e)
        return None

    if not isinstance(body, dict):
        logger.error("Recommendation events SQS message body is not an object: %s", body)
        return None
    if body.get("event_type") != RECOMMENDATION_EVENTS_EVENT_TYPE:
        logger.error("Unsupported recommendation event_type: %s", body.get("event_type"))
        return None
    if body.get("version") != RECOMMENDATION_EVENTS_VERSION:
        logger.error("Unsupported recommendation events version: %s", body.get("version"))
        return None
    raw_events = body.get("events")
    if not isinstance(raw_events, list):
        logger.error("Recommendation events SQS message has no events list: %s", body)
        return None

    events: List[RecommendationEvent] = []
    for raw_event in raw_events:
        try:
            events.append(RecommendationEvent.model_validate(raw_event))
        except ValidationError as e:
            logger.error("Skipping invalid recommendation event %s: %s", raw_event, e)
    return events
