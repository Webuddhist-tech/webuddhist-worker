"""rules-v1: hand-weighted scoring of spaces and plans for one user.

Each signal adds ``weight * strength`` to an item's score, and the signal
that added the most becomes the reason shown to the user. Weights live in
config so they can be tuned from join rates without a deploy. A learned
model would replace ``score_user`` and nothing around it.
"""
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from worker_api.config import get_float, get_int
from worker_api.recommendations.models import (
    RecDismissal,
    RecExposure,
    RecItem,
    RecMembership,
)
from worker_api.recommendations.schemas import (
    ITEM_PLAN,
    ITEM_SPACE,
    ITEM_TRADITION,
    ScoredItem,
)

REASON_CO_MEMBERS = "CO_MEMBERS"
REASON_CO_ENROLLED = "CO_ENROLLED"
REASON_TRADITION_MATCH = "TRADITION_MATCH"
REASON_FROM_JOINED_SPACE = "FROM_JOINED_SPACE"
REASON_TAG_AFFINITY = "TAG_AFFINITY"
REASON_TRENDING = "TRENDING"
REASON_NEW = "NEW"
REASON_POPULAR = "POPULAR"

ItemKey = Tuple[str, UUID]


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class ScoringContext:
    """Everything that is the same for every user, loaded once per run."""

    now: datetime
    items: Dict[ItemKey, RecItem]
    popularity: Counter
    trending: Counter
    plans_by_group: Dict[UUID, List[UUID]] = field(default_factory=dict)


def build_context(db: Session, now: Optional[datetime] = None) -> ScoringContext:
    now = now or datetime.now(timezone.utc)
    items = {(item.item_type, item.item_id): item for item in db.query(RecItem).all()}

    popularity: Counter = Counter()
    for item_type, item_id, count in db.execute(
        select(RecMembership.item_type, RecMembership.item_id, func.count())
        .where(RecMembership.item_type.in_([ITEM_SPACE, ITEM_PLAN]))
        .group_by(RecMembership.item_type, RecMembership.item_id)
    ):
        popularity[(item_type, item_id)] = count

    since = now - timedelta(days=max(get_int("RECOMMENDATION_TRENDING_WINDOW_DAYS"), 1))
    trending: Counter = Counter()
    for item_type, item_id, count in db.execute(
        select(RecMembership.item_type, RecMembership.item_id, func.count())
        .where(
            RecMembership.item_type.in_([ITEM_SPACE, ITEM_PLAN]),
            RecMembership.joined_at >= since,
        )
        .group_by(RecMembership.item_type, RecMembership.item_id)
    ):
        trending[(item_type, item_id)] = count

    plans_by_group: Dict[UUID, List[UUID]] = defaultdict(list)
    for (item_type, item_id), item in items.items():
        if item_type == ITEM_PLAN and item.group_id:
            plans_by_group[item.group_id].append(item_id)

    return ScoringContext(
        now=now,
        items=items,
        popularity=popularity,
        trending=trending,
        plans_by_group=dict(plans_by_group),
    )


class _Scores:
    """Running score per item, remembering each item's strongest signal."""

    def __init__(self) -> None:
        self.total: Dict[ItemKey, float] = defaultdict(float)
        self.best: Dict[ItemKey, Tuple[float, str, dict]] = {}

    def add(self, key: ItemKey, amount: float, reason: str, args: Optional[dict] = None) -> None:
        if amount == 0:
            return
        self.total[key] += amount
        if amount > 0 and (key not in self.best or amount > self.best[key][0]):
            self.best[key] = (amount, reason, args or {})


def _co_counts(
    db: Session, *, user_id: UUID, item_type: str, own_ids: Set[UUID]
) -> Counter:
    """For items of ``item_type``: how many people who share one of the
    user's items of that type also have it."""
    if not own_ids:
        return Counter()
    mine = aliased(RecMembership)
    theirs = aliased(RecMembership)
    rows = db.execute(
        select(theirs.item_id, func.count(func.distinct(theirs.user_id)))
        .join(mine, mine.user_id == theirs.user_id)
        .where(
            mine.item_type == item_type,
            mine.item_id.in_(own_ids),
            mine.user_id != user_id,
            theirs.item_type == item_type,
            theirs.item_id.not_in(own_ids),
        )
        .group_by(theirs.item_id)
    ).all()
    return Counter({item_id: count for item_id, count in rows})


def score_user(db: Session, ctx: ScoringContext, user_id: UUID) -> List[ScoredItem]:
    memberships = db.execute(
        select(RecMembership.item_type, RecMembership.item_id).where(
            RecMembership.user_id == user_id
        )
    ).all()
    own: Dict[str, Set[UUID]] = defaultdict(set)
    for item_type, item_id in memberships:
        own[item_type].add(item_id)
    spaces, plans, traditions = own[ITEM_SPACE], own[ITEM_PLAN], own[ITEM_TRADITION]

    dismissed = {
        (item_type, item_id)
        for item_type, item_id in db.execute(
            select(RecDismissal.item_type, RecDismissal.item_id).where(
                RecDismissal.user_id == user_id
            )
        )
    }
    ignored = {
        (item_type, item_id): impressions - clicks
        for item_type, item_id, impressions, clicks in db.execute(
            select(
                RecExposure.item_type,
                RecExposure.item_id,
                RecExposure.impressions,
                RecExposure.clicks,
            ).where(RecExposure.user_id == user_id)
        )
    }

    w_co = get_float("RECOMMENDATION_WEIGHT_CO_MEMBERS")
    w_tradition = get_float("RECOMMENDATION_WEIGHT_TRADITION")
    w_from_space = get_float("RECOMMENDATION_WEIGHT_FROM_JOINED_SPACE")
    w_tag = get_float("RECOMMENDATION_WEIGHT_TAG_AFFINITY")
    w_trending = get_float("RECOMMENDATION_WEIGHT_TRENDING")
    w_popularity = get_float("RECOMMENDATION_WEIGHT_POPULARITY")
    w_new = get_float("RECOMMENDATION_WEIGHT_NEW")
    w_ignored = get_float("RECOMMENDATION_WEIGHT_IGNORED")
    free_impressions = get_int("RECOMMENDATION_IGNORED_IMPRESSIONS_FREE")
    new_window = timedelta(days=max(get_int("RECOMMENDATION_NEW_WINDOW_DAYS"), 1))

    scores = _Scores()

    for item_id, count in _co_counts(db, user_id=user_id, item_type=ITEM_SPACE, own_ids=spaces).items():
        scores.add((ITEM_SPACE, item_id), w_co * math.log1p(count), REASON_CO_MEMBERS, {"count": count})
    for item_id, count in _co_counts(db, user_id=user_id, item_type=ITEM_PLAN, own_ids=plans).items():
        scores.add((ITEM_PLAN, item_id), w_co * math.log1p(count), REASON_CO_ENROLLED, {"count": count})

    own_tags: Counter = Counter()
    for plan_id in plans:
        item = ctx.items.get((ITEM_PLAN, plan_id))
        if item:
            own_tags.update(str(tag) for tag in item.tag_ids or [])

    for space_id in spaces:
        for plan_id in ctx.plans_by_group.get(space_id, []):
            scores.add((ITEM_PLAN, plan_id), w_from_space, REASON_FROM_JOINED_SPACE)

    for key, item in ctx.items.items():
        if item.tradition_id and item.tradition_id in traditions:
            scores.add(key, w_tradition, REASON_TRADITION_MATCH, {"tradition_id": str(item.tradition_id)})
        if key[0] == ITEM_PLAN and own_tags:
            overlap = sum(1 for tag in item.tag_ids or [] if str(tag) in own_tags)
            if overlap:
                scores.add(key, w_tag * overlap, REASON_TAG_AFFINITY, {"count": overlap})
        joins = ctx.trending.get(key, 0)
        if joins:
            scores.add(key, w_trending * math.log1p(joins), REASON_TRENDING, {"count": joins})
        members = ctx.popularity.get(key, 0)
        if members:
            scores.add(key, w_popularity * math.log1p(members), REASON_POPULAR, {"count": members})
        published_at = _aware(item.published_at)
        if published_at and ctx.now - published_at < new_window:
            freshness = 1 - (ctx.now - published_at) / new_window
            scores.add(key, w_new * freshness, REASON_NEW)
        extra_ignored = ignored.get(key, 0) - free_impressions
        if extra_ignored > 0:
            scores.add(key, -w_ignored * extra_ignored, "")

    excluded = {(ITEM_SPACE, i) for i in spaces} | {(ITEM_PLAN, i) for i in plans} | dismissed
    limit = max(get_int("RECOMMENDATION_MAX_PER_TYPE"), 1)
    ranked: Dict[str, List[ScoredItem]] = defaultdict(list)
    for key, total in sorted(scores.total.items(), key=lambda kv: kv[1], reverse=True):
        # Only live catalog items, and only ones a positive signal vouched for.
        if key in excluded or key not in ctx.items or key not in scores.best or total <= 0:
            continue
        item_type, item_id = key
        if len(ranked[item_type]) >= limit:
            continue
        _, reason, args = scores.best[key]
        ranked[item_type].append(
            ScoredItem(
                item_type=item_type,
                item_id=item_id,
                score=round(total, 4),
                reason_code=reason,
                reason_args=args,
            )
        )
    return ranked[ITEM_SPACE] + ranked[ITEM_PLAN]
