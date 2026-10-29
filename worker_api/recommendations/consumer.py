import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

import redis

from worker_api.config import get, get_bool, get_int
from worker_api.db.database import SessionLocal
from worker_api.recommendations.recompute_service import recompute_all_users, recompute_users
from worker_api.recommendations.schemas import RecommendationEvent
from worker_api.recommendations.signal_service import apply_events, purge_processed_events
from worker_api.recommendations.sqs_client import (
    delete_recommendation_events_message,
    is_recommendation_events_sqs_poll_enabled,
    parse_recommendation_events_message_body,
    receive_recommendation_events_messages,
)
from worker_api.recommendations.sync_service import catalog_is_empty, full_resync

logger = logging.getLogger(__name__)

_POLL_IDLE_SECONDS = 5
_POLL_ERROR_SECONDS = 10
# Processed event ids are kept well past SQS's 14-day maximum retention.
_PROCESSED_EVENT_RETENTION = timedelta(days=15)
_redis_client: redis.Redis | None = None


class TransientRecommendationEventsError(Exception):
    """Raised when processing should leave the SQS messages for retry."""


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(get("CACHE_CONNECTION_STRING"))
    return _redis_client


def _apply(events: List[RecommendationEvent]) -> Set[UUID]:
    with SessionLocal() as db:
        return apply_events(db, events)


async def process_recommendation_events_messages(messages: List[Dict[str, Any]]) -> None:
    """Apply a batch of messages, then rescore each affected user once.

    Rescoring per batch rather than per event means a user who joins three
    spaces in a row is scored once. Messages are deleted only after their
    events are stored; a failed rescore is not retried, since the next event
    or the full refresh covers it."""
    events: List[RecommendationEvent] = []
    handles: List[str] = []
    for message in messages:
        parsed = parse_recommendation_events_message_body(message.get("Body", ""))
        receipt_handle = message.get("ReceiptHandle")
        if parsed is None:
            if receipt_handle:
                delete_recommendation_events_message(receipt_handle)
            continue
        events.extend(parsed)
        if receipt_handle:
            handles.append(receipt_handle)

    try:
        rescore = await asyncio.to_thread(_apply, events) if events else set()
    except Exception as exc:
        raise TransientRecommendationEventsError(str(exc)) from exc

    for receipt_handle in handles:
        delete_recommendation_events_message(receipt_handle)

    if rescore:
        failed = await recompute_users(rescore)
        logger.info(
            "Applied %s recommendation event(s); rescored %s user(s), %s failed",
            len(events),
            len(rescore),
            failed,
        )


async def run_recommendation_events_sqs_consumer(stop_event: asyncio.Event) -> None:
    logger.info("Recommendation events SQS consumer started")
    while not stop_event.is_set():
        if not is_recommendation_events_sqs_poll_enabled():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_IDLE_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        try:
            messages = await asyncio.to_thread(receive_recommendation_events_messages)
            if not messages:
                continue
            try:
                await process_recommendation_events_messages(messages)
            except TransientRecommendationEventsError:
                logger.warning(
                    "Leaving %s recommendation events SQS message(s) for retry", len(messages)
                )
        except Exception:
            logger.exception("Recommendation events SQS consumer loop error")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_ERROR_SECONDS)
            except asyncio.TimeoutError:
                pass

    logger.info("Recommendation events SQS consumer stopped")


def _acquire_refresh_lock() -> Optional[bool]:
    """True when this worker holds the lock, False when another does, None
    when Redis is unreachable (the refresh then runs anyway: a duplicate
    rescore costs time, never correctness)."""
    try:
        return bool(
            _get_redis_client().set(
                get("RECOMMENDATION_FULL_REFRESH_LOCK_KEY"),
                "1",
                nx=True,
                ex=max(get_int("RECOMMENDATION_FULL_REFRESH_LOCK_TTL_SECONDS"), 60),
            )
        )
    except redis.RedisError:
        logger.warning("Redis unavailable for the recommendation refresh lock; running unlocked")
        return None


def _purge_processed() -> int:
    with SessionLocal() as db:
        return purge_processed_events(
            db, older_than=datetime.now(timezone.utc) - _PROCESSED_EVENT_RETENTION
        )


async def run_full_refresh() -> bool:
    """Resync from the backend and rescore everyone. False when another
    worker already holds the refresh."""
    if _acquire_refresh_lock() is False:
        logger.info("Another worker is running the recommendation refresh; skipping")
        return False
    await full_resync()
    await recompute_all_users()
    purged = await asyncio.to_thread(_purge_processed)
    logger.info("Recommendation refresh done; purged %s old processed event id(s)", purged)
    return True


async def run_recommendation_full_refresh_loop(stop_event: asyncio.Event) -> None:
    """Bootstraps an empty catalog straight away, then refreshes on the
    configured interval."""
    logger.info("Recommendation full refresh loop started")
    interval = max(get_int("RECOMMENDATION_FULL_REFRESH_INTERVAL_SECONDS"), 60)
    wait_seconds = 0.0
    try:
        if not await asyncio.to_thread(catalog_is_empty):
            wait_seconds = interval
    except Exception:
        logger.exception("Could not check the recommendation catalog; waiting a full interval")
        wait_seconds = interval

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
            break
        except asyncio.TimeoutError:
            pass
        wait_seconds = interval
        if not get_bool("RECOMMENDATION_FULL_REFRESH_ENABLED"):
            continue
        try:
            await run_full_refresh()
        except Exception:
            logger.exception("Recommendation full refresh failed")

    logger.info("Recommendation full refresh loop stopped")
