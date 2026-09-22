from __future__ import annotations

from datetime import datetime

from ..services import history_report as hr


def _row(hours_ago_from, score, mode="sit"):
    return hr.HistoryRow(dt=hours_ago_from, score=score, mode=mode)


def test_parse_row_normalizes_mode_case_and_aliases():
    assert hr.parse_row("2026-01-01T12:00:00", 80.0, "STANDING").mode == "stand"
    assert hr.parse_row("2026-01-01T12:00:00", 80.0, "Stand").mode == "stand"
    assert hr.parse_row("2026-01-01T12:00:00", 80.0, "sit").mode == "sit"
    assert hr.parse_row("2026-01-01T12:00:00", 80.0, "").mode == "sit"
    assert hr.parse_row("2026-01-01T12:00:00", 80.0, None).mode == "sit"


def test_parse_row_rejects_malformed_input():
    assert hr.parse_row("not-a-date", 80.0, "sit") is None
    assert hr.parse_row("2026-01-01T12:00:00", "not-a-score", "sit") is None


def test_lost_pose_excluded_from_stats_but_kept_in_parsed_rows():
    rows = [
        _row(datetime(2026, 1, 1, 9, 0), 0.0),
        _row(datetime(2026, 1, 1, 10, 0), 80.0),
    ]
    stats = hr.compute_stats(rows)
    assert stats["overall"]["count"] == 1
    assert stats["overall"]["avg"] == 80.0
    # Both rows still present for the raw table — filtering happens per-view.
    assert len(rows) == 2


def test_filter_lookback_today_excludes_yesterday():
    now = datetime(2026, 1, 10, 15, 0)
    rows = [
        _row(datetime(2026, 1, 9, 23, 59), 70.0),
        _row(datetime(2026, 1, 10, 8, 0), 90.0),
    ]
    result = hr.filter_lookback(rows, "Today", now=now)
    assert [r.score for r in result] == [90.0]


def test_filter_lookback_all_history_returns_everything():
    rows = [_row(datetime(2020, 1, 1), 50.0), _row(datetime(2026, 1, 1), 60.0)]
    assert hr.filter_lookback(rows, "All history") == rows


def test_merge_dedupe_skips_same_timestamp_and_mode():
    existing = [_row(datetime(2026, 1, 1, 12, 0, 0), 80.0, "sit")]
    new = [
        _row(datetime(2026, 1, 1, 12, 0, 0), 999.0, "sit"),  # duplicate key, skipped
        _row(datetime(2026, 1, 1, 12, 0, 0), 70.0, "stand"),  # same time, diff mode
        _row(datetime(2026, 1, 1, 13, 0, 0), 85.0, "sit"),  # new timestamp
    ]
    merged = hr.merge_dedupe(existing, new)
    assert len(merged) == 3
    assert merged[0].score == 80.0  # original kept, not overwritten by duplicate


def test_compute_stats_splits_sit_and_stand():
    rows = [
        _row(datetime(2026, 1, 1, 9, 0), 60.0, "sit"),
        _row(datetime(2026, 1, 1, 10, 0), 80.0, "sit"),
        _row(datetime(2026, 1, 1, 11, 0), 90.0, "stand"),
    ]
    stats = hr.compute_stats(rows)
    assert stats["sit"]["avg"] == 70.0
    assert stats["sit"]["count"] == 2
    assert stats["stand"]["avg"] == 90.0
    assert stats["overall"]["count"] == 3


def test_compute_stats_empty_stand_is_none_not_zero():
    rows = [_row(datetime(2026, 1, 1, 9, 0), 60.0, "sit")]
    stats = hr.compute_stats(rows)
    assert stats["stand"] is None


def test_bucket_minutes_for_uses_raw_below_40_tracked_points():
    rows = [_row(datetime(2026, 1, 1, h), 50.0) for h in range(10)]
    assert hr.bucket_minutes_for(rows, "Last 7 days") is None


def test_bucket_minutes_scales_with_lookback_span():
    rows = [_row(datetime(2026, 1, 1, 9, 0), 50.0) for _ in range(50)]
    assert hr.bucket_minutes_for(rows, "Last 24 hours") == 15
    assert hr.bucket_minutes_for(rows, "Last 7 days") == 60
    assert hr.bucket_minutes_for(rows, "Last 30 days") == 180


def test_weekday_hour_heatmap_shape_is_7x24():
    rows = [_row(datetime(2026, 1, 5, 9, 0), 50.0, "sit")]  # a Monday
    grid = hr.weekday_hour_heatmap(rows)
    assert len(grid["sit"]) == 7
    assert all(len(day) == 24 for day in grid["sit"])
    assert grid["sit"][0][9] == 50.0
    assert grid["sit"][1][9] is None
