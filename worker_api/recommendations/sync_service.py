"""Rebuild the signal tables from the backend.

Runs on bootstrap and before every full rescore. Events keep the tables
current in between; this catches whatever they missed: a lost message,
a join through a path that sends no event, an unpublished space."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from worker_api.config import get_int
from worker_api.db.database import SessionLocal
from worker_api.recommendations.backend_client import fetch_catalog_page, fetch_memberships_page
from worker_api.recommendations.models import RecItem, RecMembership
from worker_api.recommendations.schemas import (
    ITEM_PLAN,
    ITEM_SPACE,
    ITEM_TRADITION,
    CatalogItem,
    Membership,
)

logger = logging.getLogger(__name__)


async def _fetch_catalog(item_type: str, page_size: int) -> List[CatalogItem]:
    items: List[CatalogItem] = []
    after_id: Optional[UUID] = None
    while True:
        page = await fetch_catalog_page(item_type=item_type, after_id=after_id, limit=page_size)
        items.extend(page.items)
        if not page.next_after_id:
            return items
        after_id = page.next_after_id


async def _fetch_memberships(item_type: str, page_size: int) -> List[Membership]:
    memberships: List[Membership] = []
    after: Optional[str] = None
    while True:
        page = await fetch_memberships_page(item_type=item_type, after=after, limit=page_size)
        memberships.extend(page.memberships)
        if not page.next_after:
            return memberships
        after = page.next_after


def replace_catalog(db: Session, item_type: str, items: Sequence[CatalogItem]) -> int:
    """Make rec_items for ``item_type`` exactly ``items``."""
    now = datetime.now(timezone.utc)
    existing: Dict[UUID, RecItem] = {
        item.item_id: item
        for item in db.query(RecItem).filter(RecItem.item_type == item_type).all()
    }
    incoming = {item.item_id for item in items}
    for item in items:
        row = existing.get(item.item_id)
        if row is None:
            row = RecItem(item_type=item_type, item_id=item.item_id)
            db.add(row)
        row.group_id = item.group_id
        row.tradition_id = item.tradition_id
        row.language = item.language
        row.tag_ids = [str(tag) for tag in item.tag_ids]
        row.published_at = item.published_at
        row.synced_at = now
    gone = [item_id for item_id in existing if item_id not in incoming]
    if gone:
        db.execute(
            delete(RecItem).where(RecItem.item_type == item_type, RecItem.item_id.in_(gone))
        )
    db.commit()
    return len(gone)


def replace_memberships(db: Session, item_type: str, memberships: Sequence[Membership]) -> Tuple[int, int]:
    """Make rec_memberships for ``item_type`` exactly ``memberships``.
    Returns (added, removed)."""
    existing = {
        (user_id, item_id)
        for user_id, item_id in db.execute(
            select(RecMembership.user_id, RecMembership.item_id).where(
                RecMembership.item_type == item_type
            )
        )
    }
    incoming = {(m.user_id, m.item_id): m for m in memberships}
    now = datetime.now(timezone.utc)
    added = 0
    for key, membership in incoming.items():
        if key in existing:
            continue
        db.add(
            RecMembership(
                user_id=membership.user_id,
                item_type=item_type,
                item_id=membership.item_id,
                joined_at=membership.joined_at or now,
            )
        )
        added += 1
    removed = [key for key in existing if key not in incoming]
    for user_id, item_id in removed:
        db.execute(
            delete(RecMembership).where(
                RecMembership.user_id == user_id,
                RecMembership.item_type == item_type,
                RecMembership.item_id == item_id,
            )
        )
    db.commit()
    return added, len(removed)


def _apply_sync(catalogs: Dict[str, List[CatalogItem]], memberships: Dict[str, List[Membership]]) -> None:
    with SessionLocal() as db:
        for item_type, items in catalogs.items():
            gone = replace_catalog(db, item_type, items)
            logger.info("Synced %s %s item(s), dropped %s", len(items), item_type, gone)
        for item_type, rows in memberships.items():
            added, removed = replace_memberships(db, item_type, rows)
            logger.info(
                "Synced %s %s membership(s): +%s -%s", len(rows), item_type, added, removed
            )


async def full_resync() -> None:
    """Fetch everything first, then apply, so a backend error halfway leaves
    the tables as they were rather than half rebuilt."""
    page_size = max(get_int("RECOMMENDATION_SYNC_PAGE_SIZE"), 1)
    catalogs = {
        item_type: await _fetch_catalog(item_type, page_size)
        for item_type in (ITEM_SPACE, ITEM_PLAN)
    }
    memberships = {
        item_type: await _fetch_memberships(item_type, page_size)
        for item_type in (ITEM_SPACE, ITEM_PLAN, ITEM_TRADITION)
    }
    await asyncio.to_thread(_apply_sync, catalogs, memberships)


def catalog_is_empty() -> bool:
    with SessionLocal() as db:
        return db.query(RecItem.item_id).first() is None
