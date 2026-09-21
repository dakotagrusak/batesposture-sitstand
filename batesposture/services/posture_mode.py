"""Sit/stand session mode, framing hints, and presence edges.

This module does not classify sit vs stand from MediaPipe. It stores two
calibrated baselines, compares camera-space framing only as a suggestion,
and lets the tray prompt or hotkey choose the active mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from typing import Any, Mapping


class DeskMode(str, Enum):
    SIT = "sit"
    STAND = "stand"


VALID_MODES = {DeskMode.SIT.value, DeskMode.STAND.value}


def default_baseline_dict() -> dict[str, Any]:
    return {
        "posture_score": 75.0,
        "neck_angle": 10.0,
        "shoulder_delta": 0.05,
        "spine_angle": 10.0,
        "mid_shoulder_y": 0.45,
        "shoulder_width": 0.25,
        "hip_visibility": 0.0,
        "sample_count": 0,
        "calibrated": False,
    }


def coerce_baseline(raw: Any) -> dict[str, Any]:
    baseline = default_baseline_dict()
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if key not in baseline:
                continue
            if key == "calibrated":
                if isinstance(value, str):
                    baseline[key] = value.strip().lower() in {"1", "true", "yes", "on"}
                else:
                    baseline[key] = bool(value)
            elif key == "sample_count":
                try:
                    baseline[key] = int(value)
                except (TypeError, ValueError):
                    pass
            else:
                try:
                    baseline[key] = float(value)
                except (TypeError, ValueError):
                    pass
    return baseline


def baseline_is_calibrated(raw: Any) -> bool:
    return bool(coerce_baseline(raw).get("calibrated"))


@dataclass(frozen=True)
class FramingSnapshot:
    mid_shoulder_y: float
    shoulder_width: float
    hip_visibility: float
    neck_angle: float = 0.0
    spine_angle: float = 0.0

    @classmethod
    def from_metrics(cls, metrics: Mapping[str, Any] | None) -> FramingSnapshot | None:
        if not metrics:
            return None
        try:
            return cls(
                mid_shoulder_y=float(metrics.get("mid_shoulder_y", 0.45)),
                shoulder_width=float(metrics.get("shoulder_width", 0.25)),
                hip_visibility=float(metrics.get("hip_visibility", 0.0)),
                neck_angle=float(metrics.get("neck_angle", 0.0)),
                spine_angle=float(metrics.get("spine_angle", 0.0)),
            )
        except (TypeError, ValueError):
            return None


def framing_distance(snap: FramingSnapshot, baseline: Mapping[str, Any]) -> float:
    parsed = coerce_baseline(baseline)
    if not parsed["calibrated"]:
        return float("inf")
    return (
        3.0 * abs(snap.mid_shoulder_y - parsed["mid_shoulder_y"])
        + 1.5 * abs(snap.shoulder_width - parsed["shoulder_width"])
        + 1.0 * abs(snap.hip_visibility - parsed["hip_visibility"])
    )


def suggest_mode(
    snap: FramingSnapshot | None,
    sit_baseline: Mapping[str, Any],
    stand_baseline: Mapping[str, Any],
    min_gap: float = 0.12,
) -> DeskMode | None:
    """Return a mode only when one calibrated baseline is clearly closer."""
    if snap is None:
        return None
    distances = {
        DeskMode.SIT: framing_distance(snap, sit_baseline),
        DeskMode.STAND: framing_distance(snap, stand_baseline),
    }
    ranked = sorted(distances.items(), key=lambda item: item[1])
    best_mode, best = ranked[0]
    _, second = ranked[1]
    if best == float("inf"):
        return None
    if best + min_gap < second:
        return best_mode
    return None


def score_against_baseline(metrics: Mapping[str, Any], baseline: Mapping[str, Any]) -> float:
    """Penalty score relative to a calibrated good pose. 100 = matches baseline."""
    parsed = coerce_baseline(baseline)
    if not parsed["calibrated"]:
        try:
            return float(metrics.get("posture_score", 0.0))
        except (TypeError, ValueError):
            return 0.0
    try:
        neck = abs(float(metrics.get("neck_angle", 0.0)) - parsed["neck_angle"]) / 25.0
        spine = abs(float(metrics.get("spine_angle", 0.0)) - parsed["spine_angle"]) / 25.0
        shoulder = (
            abs(float(metrics.get("shoulder_vertical_delta", 0.0)) - parsed["shoulder_delta"])
            / 0.08
        )
    except (TypeError, ValueError):
        return float(metrics.get("posture_score", 0.0) or 0.0)
    penalty = 0.45 * neck + 0.35 * spine + 0.20 * shoulder
    return float(max(0.0, min(100.0, 100.0 * (1.0 - penalty))))


@dataclass
class PresenceState:
    """Debounced person-in-frame detector. Emits arrived/left once per edge."""

    absent_confirm_s: float = 2.0
    present_confirm_s: float = 0.8
    _seen: bool = False
    _edge_t: float = field(default_factory=monotonic)
    _confirmed_present: bool = False

    def update(self, person_in_frame: bool) -> str:
        now = monotonic()
        if person_in_frame != self._seen:
            self._seen = person_in_frame
            self._edge_t = now

        held = now - self._edge_t
        if person_in_frame and not self._confirmed_present and held >= self.present_confirm_s:
            self._confirmed_present = True
            return "arrived"
        if not person_in_frame and self._confirmed_present and held >= self.absent_confirm_s:
            self._confirmed_present = False
            return "left"
        return "stable"

    def reset(self) -> None:
        self._seen = False
        self._confirmed_present = False
        self._edge_t = monotonic()


def normalize_mode(value: Any, fallback: str = DeskMode.SIT.value) -> str:
    text = str(value or fallback).strip().lower()
    return text if text in VALID_MODES else fallback
