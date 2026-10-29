import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Iterable, List
from uuid import UUID

import httpx
from sqlalchemy import select

from worker_api.config import get, get_int
from worker_api.db.database import SessionLocal
from worker_api.recommendations.backend_client import put_user_recommendations
from worker_api.recommendations.models import RecMembership
from worker_api.recommendations.schemas import ScoredItem
from worker_api.recommendations.scoring import build_context, score_user

logger = logging.getLogger(__name__)

# Users scored per database pass in a full rescore, bounding memory.
_FULL_RESCORE_BATCH = 500


def _score_users(user_ids: List[UUID]) -> Dict[UUID, List[ScoredItem]]:
    with SessionLocal() as db:
        ctx = build_context(db)
        return {user_id: score_user(db, ctx, user_id) for user_id in user_ids}


async def _push(scored: Dict[UUID, List[ScoredItem]]) -> int:
    """Send each user's suggestions to the backend. Returns how many failed;
    a failure is logged and left for the next event or full rescore."""
    model = get("RECOMMENDATION_MODEL_NAME")
    computed_at = datetime.now(timezone.utc)
    semaphore = asyncio.Semaphore(max(get_int("RECOMMENDATION_PUSH_CONCURRENCY"), 1))

    async with httpx.AsyncClient(timeout=30.0) as client:

        async def push_one(user_id: UUID, items: List[ScoredItem]) -> bool:
            async with semaphore:
                try:
                    await put_user_recommendations(
                        user_id=user_id,
                        model=model,
                        computed_at=computed_at,
                        items=items,
                        client=client,
                    )
                    return True
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        # Deleted on the backend; the next resync drops them here.
                        return True
                    logger.warning("Backend rejected recommendations for %s: %s", user_id, exc)
                    return False
                except httpx.HTTPError as exc:
                    logger.warning("Failed to push recommendations for %s: %s", user_id, exc)
                    return False

        results = await asyncio.gather(
            *[push_one(user_id, items) for user_id, items in scored.items()]
        )
    return results.count(False)


async def recompute_users(user_ids: Iterable[UUID]) -> int:
    """Rescore and push the given users. Returns how many pushes failed."""
    user_ids = list(dict.fromkeys(user_ids))
    if not user_ids:
        return 0
    scored = await asyncio.to_thread(_score_users, user_ids)
    return await _push(scored)


def _all_user_ids() -> List[UUID]:
    with SessionLocal() as db:
        return list(db.execute(select(RecMembership.user_id).distinct()).scalars())


async def recompute_all_users() -> int:
    user_ids = await asyncio.to_thread(_all_user_ids)
    failed = 0
    for start in range(0, len(user_ids), _FULL_RESCORE_BATCH):
        failed += await recompute_users(user_ids[start : start + _FULL_RESCORE_BATCH])
    logger.info(
        "Rescored %s user(s) for recommendations, %s push(es) failed", len(user_ids), failed
    )
    return failed
