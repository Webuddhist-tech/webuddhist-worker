from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from starlette import status


def _resolve_timezone(timezone_name: str):
    if timezone_name in {"UTC", "Etc/UTC", "GMT"}:
        return timezone.utc
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "INVALID_TIMEZONE", "message": f"Unknown timezone: {timezone_name}"},
        ) from exc


def compute_next_trigger_at(
    routine_config: dict,
    timezone_name: str,
    after: datetime | None = None,
) -> datetime:
    times = routine_config.get("times") or []
    if not times:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "INVALID_ROUTINE", "message": "routine.times is required"},
        )

    tz = _resolve_timezone(timezone_name)

    after = after or datetime.now(timezone.utc)
    local_after = after.astimezone(tz)
    days_of_week = routine_config.get("days_of_week")

    for day_offset in range(8):
        local_date = local_after.date() + timedelta(days=day_offset)
        if days_of_week is not None and local_date.weekday() not in days_of_week:
            continue

        candidates: list[datetime] = []
        for time_value in sorted(times):
            hour, minute = map(int, time_value.split(":"))
            local_dt = datetime.combine(local_date, time(hour, minute), tzinfo=tz)
            if local_dt > local_after:
                candidates.append(local_dt)

        if candidates:
            return min(candidates).astimezone(timezone.utc)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error": "NO_UPCOMING_TRIGGER", "message": "No upcoming reminder time found"},
    )
