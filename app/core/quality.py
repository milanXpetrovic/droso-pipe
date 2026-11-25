"""Quality of one tracking result, from its trajectories: what a reviewer looks at first.

Computed when the result is stored (commit) and kept in the asset's meta["quality"]; older results
get it the first time a page needs it (ensure_quality). The verdict is a suggestion for the
reviewer, never a decision: good / check / bad, with the reasons in plain words.
"""

import numpy as np

JUMP_MM_S = 50                 # faster than a walking fly: usually an identity swap or a wrong detection
CHECK_FLAGS_PER_MIN = 10       # possible identity swaps per minute
CHECK_LOST = 0.02              # fraction of positions missing
BAD_LOST = 0.20
CHECK_JUMPS_PER_FLY_MIN = 0.5
SCALE_TOLERANCE = 0.05
COLUMNS = ["frame", "fly", "x_mm", "y_mm"]


def compute(df, fps: float, expected_flies: int | None = None, flags_per_min: float | None = None,
            ppm: float | None = None, expected_ppm: float | None = None) -> dict:
    """df: trajectories with at least frame, fly, x_mm, y_mm."""
    n_frames = int(df["frame"].nunique())
    minutes = n_frames / fps / 60 if fps else 0
    lost = float(df["x_mm"].isna().mean()) if len(df) else 1.0
    found = int(df.dropna(subset=["x_mm"])["fly"].nunique())

    d = df.sort_values(["fly", "frame"])
    g = d.groupby("fly")
    step_mm = np.hypot(g["x_mm"].diff(), g["y_mm"].diff())
    step_s = g["frame"].diff() / fps if fps else np.nan
    speed = step_mm / step_s
    jumps = int((speed > JUMP_MM_S).sum())
    jumps_rate = jumps / (max(found, 1) * minutes) if minutes else 0.0
    scale_ratio = ppm / expected_ppm if ppm and expected_ppm else None

    reasons, verdict = [], "good"

    def flag(level, text):
        nonlocal verdict
        reasons.append(text)
        if level == "bad" or verdict == "good":
            verdict = level

    if found == 0:
        flag("bad", "No fly was tracked.")
    elif expected_flies and found < expected_flies / 2:
        flag("bad", f"Only {found} of {expected_flies} flies were tracked.")
    elif expected_flies and found != expected_flies:
        flag("check", f"{found} flies tracked, {expected_flies} entered.")
    if lost > BAD_LOST:
        flag("bad", f"Flies were lost in {lost:.0%} of the frames.")
    elif lost > CHECK_LOST:
        flag("check", f"Flies were lost in {lost:.1%} of the frames.")
    if flags_per_min is not None and flags_per_min > CHECK_FLAGS_PER_MIN:
        flag("check", f"Many possible identity swaps ({flags_per_min:.0f} per minute).")
    if jumps_rate > CHECK_JUMPS_PER_FLY_MIN:
        flag("check", f"{jumps} impossible jumps (flies faster than {JUMP_MM_S} mm/s).")
    if scale_ratio is not None and abs(scale_ratio - 1) > SCALE_TOLERANCE:
        flag("check", f"The arena looks {abs(scale_ratio - 1):.0%} {'larger' if scale_ratio > 1 else 'smaller'} "
                      "than expected: it may have been found wrongly.")

    return {"verdict": verdict, "reasons": reasons, "flies_found": found, "flies_expected": expected_flies,
            "lost_fraction": round(lost, 5), "flags_per_min": flags_per_min, "jumps": jumps,
            "jumps_per_fly_min": round(jumps_rate, 3), "scale_ratio": round(scale_ratio, 4) if scale_ratio else None,
            "minutes": round(minutes, 2)}


def for_asset(path, meta: dict, params: dict) -> dict:
    import pandas as pd
    df = pd.read_parquet(path, columns=COLUMNS)
    return compute(df, fps=params.get("fps") or meta.get("fps"), expected_flies=params.get("n_flies"),
                   flags_per_min=meta.get("flags_per_min"),
                   ppm=(meta.get("calibration") or {}).get("ppm"), expected_ppm=params.get("expected_ppm"))


def ensure_quality(asset) -> dict | None:
    """The asset's quality, computed and stored now if it was made before quality existed."""
    if asset.meta.get("quality") is not None:
        return asset.meta["quality"]
    from .storage import resolve
    try:
        q = for_asset(resolve(asset.rel_path), asset.meta, asset.produced_by.params)
    except Exception:  # noqa: BLE001 - a missing or unreadable file must not break a page
        return None
    asset.meta = {**asset.meta, "quality": q}
    asset.save(update_fields=["meta"])
    return q
