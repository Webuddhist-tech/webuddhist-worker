from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel

ITEM_SPACE = "SPACE"
ITEM_PLAN = "PLAN"
ITEM_TRADITION = "TRADITION"

ACTION_JOIN = "JOIN"
ACTION_LEAVE = "LEAVE"
ACTION_START = "START"
ACTION_UNENROLL = "UNENROLL"
ACTION_FOLLOW = "FOLLOW"
ACTION_UNFOLLOW = "UNFOLLOW"
ACTION_IMPRESSION = "IMPRESSION"
ACTION_CLICK = "CLICK"
ACTION_DISMISS = "DISMISS"

MEMBERSHIP_ADD_ACTIONS = frozenset({ACTION_JOIN, ACTION_START, ACTION_FOLLOW})
MEMBERSHIP_REMOVE_ACTIONS = frozenset({ACTION_LEAVE, ACTION_UNENROLL, ACTION_UNFOLLOW})
# Actions that change what a user should be suggested, so they rescore now.
RESCORE_ACTIONS = MEMBERSHIP_ADD_ACTIONS | MEMBERSHIP_REMOVE_ACTIONS | {ACTION_DISMISS}


class RecommendationEvent(BaseModel):
    event_id: UUID
    user_id: UUID
    item_type: str
    item_id: UUID
    action: str
    source: Optional[str] = None
    occurred_at: Optional[datetime] = None


class CatalogItem(BaseModel):
    item_type: str
    item_id: UUID
    group_id: Optional[UUID] = None
    tradition_id: Optional[UUID] = None
    language: Optional[str] = None
    tag_ids: List[UUID] = []
    published_at: Optional[datetime] = None


class CatalogPage(BaseModel):
    items: List[CatalogItem]
    next_after_id: Optional[UUID] = None


class Membership(BaseModel):
    user_id: UUID
    item_id: UUID
    joined_at: Optional[datetime] = None


class MembershipsPage(BaseModel):
    item_type: str
    memberships: List[Membership]
    next_after: Optional[str] = None


class ScoredItem(BaseModel):
    item_type: str
    item_id: UUID
    score: float
    reason_code: str
    reason_args: Dict[str, Any] = {}
