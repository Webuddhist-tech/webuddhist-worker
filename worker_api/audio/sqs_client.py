import json
import logging
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from worker_api.config import get, get_bool, get_int

logger = logging.getLogger(__name__)

_sqs_client = None


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


def get_audio_sqs_queue_url() -> str:
    return get("AUDIO_SQS_QUEUE_URL").strip()


def is_audio_sqs_configured() -> bool:
    return bool(get_audio_sqs_queue_url())


def is_audio_sqs_poll_enabled() -> bool:
    return get_bool("AUDIO_SQS_POLL_ENABLED") and is_audio_sqs_configured()


def receive_audio_job_messages() -> List[Dict[str, Any]]:
    queue_url = get_audio_sqs_queue_url()
    if not queue_url:
        return []

    try:
        response = _get_sqs_client().receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=get_int("AUDIO_SQS_MAX_MESSAGES"),
            WaitTimeSeconds=get_int("AUDIO_SQS_WAIT_TIME_SECONDS"),
            VisibilityTimeout=get_int("AUDIO_SQS_VISIBILITY_TIMEOUT_SECONDS"),
            MessageAttributeNames=["All"],
        )
        return response.get("Messages", [])
    except ClientError as e:
        logger.error("Failed to receive audio SQS messages: %s", e)
        return []


def delete_audio_job_message(receipt_handle: str) -> None:
    queue_url = get_audio_sqs_queue_url()
    if not queue_url or not receipt_handle:
        return

    try:
        _get_sqs_client().delete_message(
            QueueUrl=queue_url,
            ReceiptHandle=receipt_handle,
        )
    except ClientError as e:
        logger.error("Failed to delete audio SQS message: %s", e)


def parse_audio_job_message_body(raw_body: str) -> Optional[Dict[str, Any]]:
    try:
        body = json.loads(raw_body)
    except (TypeError, json.JSONDecodeError) as e:
        logger.error("Invalid audio SQS message body: %s", e)
        return None

    if not isinstance(body, dict) or not body.get("job_id"):
        logger.error("Audio SQS message missing job_id: %s", body)
        return None
    return body
