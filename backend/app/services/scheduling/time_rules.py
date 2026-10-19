"""UTC persistence and calendar-based wall times; DST gaps skip, folds use the first instant."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
# Explicit aliases, not guessed UTC offsets. Other Windows names must be supplied
# as IANA IDs (the desktop reports Intl's local IANA zone). No system settings change.
WINDOWS_ZONES = {
    "UTC": "Etc/UTC",
    "China Standard Time": "Asia/Shanghai",
    "Taipei Standard Time": "Asia/Taipei",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "India Standard Time": "Asia/Kolkata",
    "Singapore Standard Time": "Asia/Singapore",
    "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "New Zealand Standard Time": "Pacific/Auckland",
}


def zone_name(value):
    name = WINDOWS_ZONES.get(value, value)
    try:
        ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("SCHEDULE_TIMEZONE_UNAVAILABLE") from None
    return name


def utc(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ValueError("SCHEDULE_UTC_REQUIRED")
    return result.astimezone(UTC)


def stamp(value):
    return utc(value).isoformat(timespec="microseconds")


def wall_instant(value, name):
    """Round trip detects missing wall times. fold=0 deliberately runs once per local date."""
    if value.tzinfo is not None:
        raise ValueError("SCHEDULE_LOCAL_TIME_REQUIRED")
    zone = ZoneInfo(zone_name(name))
    selected = value.replace(tzinfo=zone, fold=0).astimezone(UTC)
    if selected.astimezone(zone).replace(tzinfo=None) != value:
        return None
    return selected


def next_fire(definition, after, anchor):
    """Return the first instant strictly after `after`; intervals retain their UTC anchor."""
    after, anchor = utc(after), utc(anchor)
    config, kind = definition.trigger_config, definition.trigger_type
    if kind == "CONDITION":
        return after + timedelta(seconds=config.poll_interval)
    if kind == "ONCE":
        chosen = wall_instant(datetime.fromisoformat(config.at), definition.timezone)
        return chosen if chosen is not None and chosen > after else None
    if kind == "INTERVAL":
        start = utc(config.start_at) if config.start_at else anchor
        seconds = config.interval_seconds
        count = max(1, int((after - start).total_seconds() // seconds) + 1)
        return start + timedelta(seconds=count * seconds)
    zone = ZoneInfo(definition.timezone)
    today = after.astimezone(zone).date()
    clock = time.fromisoformat(config.time)
    for offset in range(371):
        day = today + timedelta(days=offset)
        if kind == "WEEKLY" and day.weekday() not in config.weekdays:
            continue
        chosen = wall_instant(datetime.combine(day, clock), definition.timezone)
        if chosen is not None and chosen > after:
            return chosen
    raise ValueError("SCHEDULE_TIME_RANGE")


def first_fire(definition, now):
    if definition.trigger_type == "ONCE":
        return wall_instant(
            datetime.fromisoformat(definition.trigger_config.at), definition.timezone
        )
    return next_fire(definition, now, now)


def preview(definition, now, count=3):
    selected, result = first_fire(definition, now), []
    for _ in range(min(count, 5)):
        if selected is None:
            break
        result.append(
            {
                "utc": stamp(selected),
                "local": selected.astimezone(ZoneInfo(definition.timezone)).isoformat(),
            }
        )
        selected = next_fire(definition, selected, now)
    return result
