from datetime import datetime
from typing import List, Optional
from uuid import UUID

import httpx

from worker_api.notifications.services.backend_client import _backend_headers, _backend_url
from worker_api.recommendations.schemas import CatalogPage, MembershipsPage, ScoredItem


async def fetch_catalog_page(
    *, item_type: str, after_id: Optional[UUID], limit: int
) -> CatalogPage:
    params = {"item_type": item_type, "limit": limit}
    if after_id:
        params["after_id"] = str(after_id)
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/recommendations/catalog",
            params=params,
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return CatalogPage.model_validate(response.json())


async def fetch_memberships_page(
    *, item_type: str, after: Optional[str], limit: int
) -> MembershipsPage:
    params = {"item_type": item_type, "limit": limit}
    if after:
        params["after"] = after
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/recommendations/memberships",
            params=params,
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return MembershipsPage.model_validate(response.json())


async def put_user_recommendations(
    *,
    user_id: UUID,
    model: str,
    computed_at: datetime,
    items: List[ScoredItem],
    client: Optional[httpx.AsyncClient] = None,
) -> None:
    """Replace the user's suggestions on the backend. Pass ``client`` to reuse
    one connection pool across a full rescore."""
    payload = {
        "model": model,
        "computed_at": computed_at.isoformat(),
        "items": [item.model_dump(mode="json") for item in items],
    }
    url = f"{_backend_url()}/internal/recommendations/users/{user_id}"
    if client is not None:
        response = await client.put(url, json=payload, headers=_backend_headers())
        response.raise_for_status()
        return
    async with httpx.AsyncClient(timeout=30.0) as own_client:
        response = await own_client.put(url, json=payload, headers=_backend_headers())
        response.raise_for_status()
