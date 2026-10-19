from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.scheduling.contracts import ScheduleDefinition, Trigger
from app.services.scheduling.time_rules import (
    first_fire,
    next_fire,
    preview,
    wall_instant,
    zone_name,
)

UTC = timezone.utc


def definition(kind="DAILY", **config):
    return ScheduleDefinition(
        name="Synthetic schedule",
        conversation_id=str(uuid4()),
        workflow_id=str(uuid4()),
        workflow_version_id=str(uuid4()),
        trigger_type=kind,
        timezone="America/New_York",
        trigger_config=Trigger(**config),
    )


def test_dst_gap_is_skipped_and_fold_runs_only_first_instant():
    assert wall_instant(datetime(2026, 3, 8, 2, 30), "America/New_York") is None
    first = wall_instant(datetime(2026, 11, 1, 1, 30), "America/New_York")
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    graph = definition(time="01:30")
    assert next_fire(graph, first, first) == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)
    gap = definition(time="02:30")
    assert next_fire(gap, datetime(2026, 3, 8, tzinfo=UTC), first) == datetime(
        2026, 3, 9, 6, 30, tzinfo=UTC
    )


def test_calendar_days_are_not_24_hour_utc_increments():
    graph = definition(time="09:00")
    before = datetime(2026, 3, 7, 14, tzinfo=UTC)
    assert next_fire(graph, before, before) - before == timedelta(hours=23)
    graph = definition("WEEKLY", time="09:00", weekdays=[0])
    assert next_fire(graph, datetime(2026, 10, 19, 14, tzinfo=UTC), before) == datetime(
        2026, 10, 26, 13, tzinfo=UTC
    )


def test_windows_alias_iana_mapping_and_preview_are_real():
    assert zone_name("China Standard Time") == "Asia/Shanghai"
    graph = definition(time="09:00").model_copy(update={"timezone": "Asia/Shanghai"})
    rows = preview(graph, datetime(2026, 10, 19, tzinfo=UTC))
    assert len(rows) == 3 and rows[0]["utc"].startswith("2026-10-19T01:00")
    assert rows[0]["local"].endswith("+08:00")
    with pytest.raises(ValueError):
        zone_name("Unknown Windows Time")


def test_interval_uses_anchor_and_clock_jump_never_replays_old_slot():
    graph = definition("INTERVAL", interval_seconds=300)
    start = datetime(2026, 10, 19, tzinfo=UTC)
    assert first_fire(graph, start) == start + timedelta(minutes=5)
    assert next_fire(graph, start + timedelta(days=3, seconds=1), start) == start + timedelta(
        days=3, minutes=5
    )


@pytest.mark.parametrize(
    "kind,config",
    [
        ("INTERVAL", {"interval_seconds": 1}),
        ("WEEKLY", {"time": "09:00", "weekdays": []}),
        ("DAILY", {"time": "25:00"}),
        ("ONCE", {"at": "2026-03-08T02:30"}),
        ("ONCE", {"at": "2026-10-19T09:00+08:00"}),
        ("CONDITION", {"condition_type": "FILE_CHANGE"}),
    ],
)
def test_invalid_time_configuration_is_rejected(kind, config):
    with pytest.raises(ValueError):
        definition(kind, **config)
