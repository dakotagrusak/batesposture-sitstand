from ..services.posture_mode import (
    DeskMode,
    FramingSnapshot,
    PresenceState,
    coerce_baseline,
    framing_distance,
    normalize_mode,
    score_against_baseline,
    suggest_mode,
)


def test_normalize_mode_rejects_junk() -> None:
    assert normalize_mode("STAND") == "stand"
    assert normalize_mode("nope") == "sit"


def test_suggest_mode_requires_clear_gap() -> None:
    sit = coerce_baseline(
        {
            "mid_shoulder_y": 0.55,
            "shoulder_width": 0.22,
            "hip_visibility": 0.2,
            "calibrated": True,
        }
    )
    stand = coerce_baseline(
        {
            "mid_shoulder_y": 0.32,
            "shoulder_width": 0.24,
            "hip_visibility": 0.8,
            "calibrated": True,
        }
    )
    snap = FramingSnapshot(mid_shoulder_y=0.33, shoulder_width=0.24, hip_visibility=0.75)
    assert suggest_mode(snap, sit, stand) is DeskMode.STAND
    ambiguous = FramingSnapshot(mid_shoulder_y=0.44, shoulder_width=0.23, hip_visibility=0.5)
    assert suggest_mode(ambiguous, sit, stand) is None


def test_framing_distance_infinite_when_uncalibrated() -> None:
    snap = FramingSnapshot(0.4, 0.2, 0.5)
    assert framing_distance(snap, {"calibrated": False}) == float("inf")


def test_score_against_baseline_is_100_when_identical() -> None:
    baseline = coerce_baseline(
        {
            "neck_angle": 12.0,
            "spine_angle": 8.0,
            "shoulder_delta": 0.02,
            "calibrated": True,
        }
    )
    metrics = {
        "neck_angle": 12.0,
        "spine_angle": 8.0,
        "shoulder_vertical_delta": 0.02,
        "posture_score": 40.0,
    }
    assert score_against_baseline(metrics, baseline) == 100.0


def test_presence_emits_left_and_arrived(monkeypatch) -> None:
    state = PresenceState(absent_confirm_s=0.0, present_confirm_s=0.0)
    # Force confirmed present first.
    assert state.update(True) == "arrived"
    assert state.update(False) == "left"
    assert state.update(True) == "arrived"
