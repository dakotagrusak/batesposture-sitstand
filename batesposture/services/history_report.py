"""Local posture-history analysis: parsing, lookback filtering, bucketing,
stats, and CSV merge/dedupe.

Mirrors the rules the standalone "Stance" report used, so the numbers here
match what that one-off web report would have shown, computed locally
instead. No network calls, no cloud storage — everything here reads rows
already logged to BatesPosture's own SQLite database or CSV exports.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean, pstdev

LOST_POSE_THRESHOLD = 1.0
MODES = ("sit", "stand")

LOOKBACKS = (
    "Today",
    "Last 24 hours",
    "Last 3 days",
    "Last 7 days",
    "Last 14 days",
    "Last 30 days",
    "All history",
)

CANDLE_INTERVALS = (
    ("1 minute", 1),
    ("5 minutes", 5),
)

# Bollinger-style band defaults: a rolling mean over the trailing `window`
# candles, bounded by +/- `num_std` standard deviations of that same window.
BOLLINGER_WINDOW = 20
BOLLINGER_NUM_STD = 2.0

# A slump is a tracked score below this, inside one session. Matches the
# default poor-posture alert so History and live alerts describe the same thing.
SLUMP_THRESHOLD = 60.0
SESSION_GAP_MULT = 3.0
MIN_SLUMP_POINTS = 2

_FIXED_SPANS = {
    "Last 24 hours": timedelta(hours=24),
    "Last 3 days": timedelta(days=3),
    "Last 7 days": timedelta(days=7),
    "Last 14 days": timedelta(days=14),
    "Last 30 days": timedelta(days=30),
}


@dataclass(frozen=True)
class HistoryRow:
    dt: datetime
    score: float
    mode: str  # "sit" or "stand"

    @property
    def lost(self) -> bool:
        """A score this low means the pose was lost, not that posture slumped."""
        return self.score <= LOST_POSE_THRESHOLD


def normalize_mode(raw: str | None) -> str:
    text = (raw or "").strip().lower()
    return "stand" if text in ("stand", "standing") else "sit"


def parse_row(timestamp: str, score: float, mode: str) -> HistoryRow | None:
    try:
        dt = datetime.fromisoformat(str(timestamp))
    except ValueError:
        return None
    try:
        score_f = float(score)
    except (TypeError, ValueError):
        return None
    return HistoryRow(dt=dt, score=score_f, mode=normalize_mode(mode))


def parse_rows(records) -> list[HistoryRow]:
    """records: iterable of (timestamp, score, mode) tuples, e.g. DB rows."""
    parsed = []
    for timestamp, score, mode in records:
        row = parse_row(timestamp, score, mode)
        if row is not None:
            parsed.append(row)
    return parsed


def load_csv(path: str) -> list[HistoryRow]:
    """Parse a BatesPosture export CSV (timestamp,score,mode)."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows: list[HistoryRow] = []
        for i, record in enumerate(reader):
            if i == 0 and record[:3] == ["timestamp", "score", "mode"]:
                continue
            if len(record) < 3:
                continue
            row = parse_row(record[0], record[1], record[2])
            if row is not None:
                rows.append(row)
        return rows


def merge_dedupe(existing: list[HistoryRow], new: list[HistoryRow]) -> list[HistoryRow]:
    """Combine two row sets, deduping on floor(unix_seconds)|mode."""

    def key(row: HistoryRow) -> tuple[int, str]:
        return int(row.dt.timestamp()), row.mode

    seen = {key(r) for r in existing}
    merged = list(existing)
    for row in new:
        k = key(row)
        if k in seen:
            continue
        seen.add(k)
        merged.append(row)
    merged.sort(key=lambda r: r.dt)
    return merged


def filter_lookback(
    rows: list[HistoryRow], label: str, now: datetime | None = None
) -> list[HistoryRow]:
    now = now or datetime.now()
    if label == "All history":
        return list(rows)
    if label == "Today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - _FIXED_SPANS[label]
    return [r for r in rows if start <= r.dt <= now]


def lookback_span(
    rows: list[HistoryRow], label: str, now: datetime | None = None
) -> timedelta:
    """The span implied by the selected lookback, used to pick a bucket size."""
    now = now or datetime.now()
    if label == "Today":
        return now - now.replace(hour=0, minute=0, second=0, microsecond=0)
    if label in _FIXED_SPANS:
        return _FIXED_SPANS[label]
    # "All history": no fixed span, use the actual data extent.
    if not rows:
        return timedelta(0)
    return max(r.dt for r in rows) - min(r.dt for r in rows)


def bucket_minutes_for(
    rows: list[HistoryRow], label: str, now: datetime | None = None
) -> int | None:
    """None means \"too few points to bucket — plot raw samples\"."""
    tracked = [r for r in rows if not r.lost]
    if len(tracked) < 40:
        return None
    hours = lookback_span(rows, label, now).total_seconds() / 3600
    if hours <= 8:
        return 5
    if hours <= 48:
        return 15
    if hours <= 240:  # 10 days
        return 60
    return 180


def _bucket_start(dt: datetime, minutes: int) -> datetime:
    epoch_minutes = int(dt.timestamp() // 60)
    bucketed = (epoch_minutes // minutes) * minutes
    return datetime.fromtimestamp(bucketed * 60)


def timeseries_by_mode(
    rows: list[HistoryRow], bucket_minutes: int | None
) -> dict[str, list[tuple[datetime, float]]]:
    tracked = [r for r in rows if not r.lost]
    result: dict[str, list[tuple[datetime, float]]] = {}
    for mode in MODES:
        subset = sorted((r for r in tracked if r.mode == mode), key=lambda r: r.dt)
        if bucket_minutes is None:
            result[mode] = [(r.dt, r.score) for r in subset]
            continue
        buckets: dict[datetime, list[float]] = {}
        for r in subset:
            buckets.setdefault(_bucket_start(r.dt, bucket_minutes), []).append(r.score)
        result[mode] = [(t, mean(scores)) for t, scores in sorted(buckets.items())]
    return result


@dataclass(frozen=True)
class OHLCBar:
    t: datetime
    open: float
    high: float
    low: float
    close: float
    n: int


def ohlc_by_mode(rows: list[HistoryRow], bucket_minutes: int) -> dict[str, list[OHLCBar]]:
    """Open/high/low/close per bucket, per mode — candlestick input.

    Open is the first tracked score chronologically in the bucket, close the
    last; a bucket with a single point (e.g. 1-minute candles over 1-minute
    logging) has open == high == low == close.
    """
    tracked = [r for r in rows if not r.lost]
    result: dict[str, list[OHLCBar]] = {}
    for mode in MODES:
        subset = sorted((r for r in tracked if r.mode == mode), key=lambda r: r.dt)
        buckets: dict[datetime, list[float]] = {}
        for r in subset:
            buckets.setdefault(_bucket_start(r.dt, bucket_minutes), []).append(r.score)
        bars = [
            OHLCBar(
                t=t,
                open=scores[0],
                high=max(scores),
                low=min(scores),
                close=scores[-1],
                n=len(scores),
            )
            for t, scores in sorted(buckets.items())
        ]
        result[mode] = bars
    return result


@dataclass(frozen=True)
class BollingerBand:
    t: datetime
    mean: float
    upper: float
    lower: float


def bollinger_bands(
    values: list[tuple[datetime, float]],
    window: int = BOLLINGER_WINDOW,
    num_std: float = BOLLINGER_NUM_STD,
) -> list[BollingerBand]:
    """Rolling mean +/- num_std * stddev over a trailing window of points.

    Same shape as a financial Bollinger Band: the middle line is a simple
    moving average of the last `window` values, and the bounds are that
    average +/- `num_std` standard deviations computed over the same window.
    """
    scores = [v for _, v in values]
    bands: list[BollingerBand] = []
    for i in range(len(values)):
        segment = scores[max(0, i - window + 1) : i + 1]
        if len(segment) < 2:
            continue
        m = mean(segment)
        sd = pstdev(segment)
        bands.append(
            BollingerBand(t=values[i][0], mean=m, upper=m + num_std * sd, lower=m - num_std * sd)
        )
    return bands


def daily_means(rows: list[HistoryRow]) -> dict[str, list[tuple[date, float]]]:
    tracked = [r for r in rows if not r.lost]
    result: dict[str, list[tuple[date, float]]] = {}
    for mode in MODES:
        buckets: dict[date, list[float]] = {}
        for r in tracked:
            if r.mode == mode:
                buckets.setdefault(r.dt.date(), []).append(r.score)
        result[mode] = [(d, mean(v)) for d, v in sorted(buckets.items())]
    return result


def hour_of_day_means(rows: list[HistoryRow]) -> dict[str, list[tuple[int, float]]]:
    tracked = [r for r in rows if not r.lost]
    result: dict[str, list[tuple[int, float]]] = {}
    for mode in MODES:
        buckets: dict[int, list[float]] = {h: [] for h in range(24)}
        for r in tracked:
            if r.mode == mode:
                buckets[r.dt.hour].append(r.score)
        result[mode] = [(h, mean(v)) for h, v in sorted(buckets.items()) if v]
    return result


def weekday_hour_heatmap(rows: list[HistoryRow]) -> dict[str, list[list[float | None]]]:
    """7x24 grid (Monday=0..Sunday=6) of mean score per mode; None where no data."""
    tracked = [r for r in rows if not r.lost]
    result: dict[str, list[list[float | None]]] = {}
    for mode in MODES:
        grid: list[list[list[float]]] = [[[] for _ in range(24)] for _ in range(7)]
        for r in tracked:
            if r.mode == mode:
                grid[r.dt.weekday()][r.dt.hour].append(r.score)
        result[mode] = [[mean(cell) if cell else None for cell in day] for day in grid]
    return result


def histogram(rows: list[HistoryRow], bin_size: int = 10) -> dict[str, list[tuple[int, int]]]:
    """Score histogram per mode: (bin_start, count) for bins covering 0-100."""
    tracked = [r for r in rows if not r.lost]
    edges = list(range(0, 101, bin_size))
    result: dict[str, list[tuple[int, int]]] = {}
    for mode in MODES:
        counts = {edge: 0 for edge in edges[:-1]}
        for r in tracked:
            if r.mode != mode:
                continue
            score = min(max(r.score, 0.0), 100.0 - 1e-9)
            bin_start = edges[int(score // bin_size)]
            counts[bin_start] += 1
        result[mode] = sorted(counts.items())
    return result


def _agg(rows: list[HistoryRow]) -> dict | None:
    if not rows:
        return None
    scores = [r.score for r in rows]
    return {
        "count": len(scores),
        "avg": mean(scores),
        "min": min(scores),
        "max": max(scores),
    }


def early_late_delta(rows: list[HistoryRow]) -> float | None:
    """Late-third mean minus early-third mean, chronological, tracked scores only."""
    ordered = sorted(rows, key=lambda r: r.dt)
    third = len(ordered) // 3
    if third == 0:
        return None
    early = ordered[:third]
    late = ordered[-third:]
    return mean(r.score for r in late) - mean(r.score for r in early)


def compute_stats(rows: list[HistoryRow]) -> dict:
    tracked = [r for r in rows if not r.lost]
    return {
        "overall": _agg(tracked),
        "sit": _agg([r for r in tracked if r.mode == "sit"]),
        "stand": _agg([r for r in tracked if r.mode == "stand"]),
        "delta": early_late_delta(tracked),
    }


@dataclass(frozen=True)
class CoverageSpan:
    """One stretch of the clock: tracking, lost pose, or a hole in the log."""

    start: datetime
    end: datetime
    state: str  # "tracked" | "lost" | "gap"


def _median_step_seconds(rows: list[HistoryRow]) -> float:
    deltas = sorted(
        (b.dt - a.dt).total_seconds()
        for a, b in zip(rows, rows[1:])
        if (b.dt - a.dt).total_seconds() > 0
    )
    if not deltas:
        return 60.0
    # Drop the longest quarter so a lunch hole does not become "the" step.
    keep = deltas[: max(1, int(len(deltas) * 0.75))]
    return float(keep[len(keep) // 2])


def coverage_spans(
    rows: list[HistoryRow], gap_mult: float = SESSION_GAP_MULT
) -> list[CoverageSpan]:
    """Sessionize the raw log into tracked / lost-pose / gap spans.

    Gaps are holes longer than ``gap_mult`` times the median step. They are
    not slumps — tracking was off (lock screen, Stop tracking, walked away).
    """
    ordered = sorted(rows, key=lambda r: r.dt)
    if not ordered:
        return []
    step = _median_step_seconds(ordered)
    cut = max(step * gap_mult, step + 1)
    spans: list[CoverageSpan] = []
    run_start = ordered[0]
    prev = ordered[0]

    def _flush(start: HistoryRow, end: HistoryRow) -> None:
        state = "lost" if start.lost else "tracked"
        # A mixed run is split at the first state change by the loop below.
        spans.append(CoverageSpan(start=start.dt, end=end.dt, state=state))

    for row in ordered[1:]:
        gap = (row.dt - prev.dt).total_seconds()
        same_state = row.lost == run_start.lost
        if gap > cut:
            _flush(run_start, prev)
            spans.append(CoverageSpan(start=prev.dt, end=row.dt, state="gap"))
            run_start = row
        elif not same_state:
            _flush(run_start, prev)
            run_start = row
        prev = row
    _flush(run_start, prev)
    return spans


@dataclass(frozen=True)
class SlumpEpisode:
    start: datetime
    end: datetime
    mode: str
    n: int
    min_score: float
    duration_s: float


def slump_episodes(
    rows: list[HistoryRow],
    threshold: float = SLUMP_THRESHOLD,
    min_points: int = MIN_SLUMP_POINTS,
    gap_mult: float = SESSION_GAP_MULT,
) -> list[SlumpEpisode]:
    """Runs of tracked scores below ``threshold``, never crossing a session gap.

    Lost-pose rows are skipped. A lunch-sized hole ends the episode rather
    than stretching it across empty time.
    """
    tracked = sorted((r for r in rows if not r.lost), key=lambda r: r.dt)
    if not tracked:
        return []
    step = _median_step_seconds(tracked)
    cut = max(step * gap_mult, step + 1)
    episodes: list[SlumpEpisode] = []

    current: list[HistoryRow] = []

    def _close() -> None:
        if len(current) < min_points:
            current.clear()
            return
        start, end = current[0], current[-1]
        episodes.append(
            SlumpEpisode(
                start=start.dt,
                end=end.dt,
                mode=start.mode,
                n=len(current),
                min_score=min(r.score for r in current),
                duration_s=(end.dt - start.dt).total_seconds() or step,
            )
        )
        current.clear()

    prev: HistoryRow | None = None
    for row in tracked:
        if prev is not None and (row.dt - prev.dt).total_seconds() > cut:
            _close()
        if row.score < threshold and (not current or current[0].mode == row.mode):
            current.append(row)
        else:
            _close()
            if row.score < threshold:
                current.append(row)
        prev = row
    _close()
    return episodes


def slump_duration_histogram(
    episodes: list[SlumpEpisode],
) -> dict[str, list[tuple[float, int]]]:
    """Duration buckets in minutes, per mode. Edges: 1, 2, 5, 10, 20, 40+."""
    edges = (1.0, 2.0, 5.0, 10.0, 20.0, 40.0)
    result: dict[str, list[tuple[float, int]]] = {}
    for mode in MODES:
        counts = {e: 0 for e in edges}
        for ep in episodes:
            if ep.mode != mode:
                continue
            minutes = ep.duration_s / 60.0
            bucket = edges[-1]
            for edge in edges:
                if minutes <= edge:
                    bucket = edge
                    break
            counts[bucket] += 1
        result[mode] = [(e, counts[e]) for e in edges]
    return result


def export_csv(rows: list[HistoryRow], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "score", "mode"])
        for r in sorted(rows, key=lambda r: r.dt):
            writer.writerow([r.dt.isoformat(), r.score, r.mode])
