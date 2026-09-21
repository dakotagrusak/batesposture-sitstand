# Sit / stand patch for BatesPosture

Hand this folder to Claude Code. The patch is already applied on top of
`wtbates99/batesposture` at commit `fb4f117`.

## What changed

BatesPosture now has two calibrated desk modes (`sit`, `stand`), a tray menu to
switch them, a prompt when you come back into frame, and a `mode` column on
logged scores / CSV export.

It does **not** auto-classify sit vs stand from MediaPipe. The webcam can only
hint. The user confirms.

## Files touched

| File | Role |
|---|---|
| `batesposture/services/posture_mode.py` | **new** mode helpers, framing hint, presence edges |
| `batesposture/services/settings_service.py` | `active_mode`, `prompt_on_return`, `sit_baseline`, `stand_baseline`, schema `1.2.0`, migrate old single baseline → sit |
| `batesposture/ml/pose_detector.py` | extra framing metrics: `mid_shoulder_y`, `shoulder_width`, `hip_visibility` |
| `batesposture/data/database.py` | `posture_scores.mode`, CSV header `timestamp,score,mode` |
| `batesposture/ui/onboarding.py` | `mode="sit"|"stand"` calibration wizard |
| `batesposture/ui/tray.py` | Desk Mode menu, return prompt, hotkeys, mode-tagged DB writes |
| `batesposture/tests/test_posture_mode.py` | **new** unit tests |

## UX after the patch

Tray → **Desk Mode**

- Sitting (`Ctrl+Alt+1`)
- Standing (`Ctrl+Alt+2`)
- Recalibrate this mode… (`Ctrl+Alt+R`)
- Prompt when I sit back down (checkbox)

If a mode has never been calibrated, choosing it opens the 6-second wizard for
that mode. After you walk away for ~2s and come back, the app pauses scoring
and asks Sitting / Standing / Recalibrate / Keep last mode. Suggestion is only
shown when framing is clearly closer to one baseline.

## Claude Code tasks if you need to keep going

1. Run tests:
   `uv sync --locked --all-groups && QT_QPA_PLATFORM=offscreen uv run python -m pytest`
2. If settings tests fail, they likely construct `UserProfileSettings` with a
   closed field set — add the new fields or ignore extras.
3. Do not add a sit/stand neural net. If you want smarter switching, tighten
   `suggest_mode()` in `posture_mode.py`.
4. Dashboard still reads legacy `baseline_*` fields. Those are synced from the
   active mode baseline in settings normalization and `_choose_mode()`.
5. Keep logging opt-in. Default DB write interval is still 900s; lower
   `db_write_interval_seconds` if you want a real timeline.

## Apply on a clean clone

If this working tree is not what you have:

```bash
git clone https://github.com/wtbates99/batesposture.git
cd batesposture
git apply /path/to/sit-stand.patch
```

The unified diff is `sit-stand.patch` in this repo root (generated from
`git diff`).
