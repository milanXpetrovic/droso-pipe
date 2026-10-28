"""Data formats (asset types) known to the system, with their validators.

When the worker and external tools arrive, this moves into the shared contracts/ package;
until then the app is the only producer and consumer of these formats.
"""

from .models import AssetType

RAW_VIDEO = ("raw_video", 1)
CROP_VIDEO = ("crop_video", 1)
TRAJECTORIES = ("trajectories", 1)
FT_TRACK = ("flytracker_track", 1)
FT_FEAT = ("flytracker_feat", 1)
FT_CALIBRATION = ("flytracker_calibration", 1)

SPECS = {
    RAW_VIDEO: {
        "name": "Raw video from a rig camera",
        "spec": {
            "files": {"video.<ext>": "the recording, unchanged, read-only",
                      "recording.json": "metadata of the recording at ingest time",
                      "manifest.json": "this asset: checksum, size, meta, producing run"},
            "meta": {"duration_s": "float > 0", "fps": "float > 0, as declared by the file",
                     "n_frames": "int > 0, counted", "measured_fps": "n_frames / duration_s",
                     "width": "int", "height": "int", "codec": "str", "source_file_name": "str"},
        },
    },
    CROP_VIDEO: {
        "name": "One arena cut out of a raw video",
        "spec": {
            "files": {"A01.mp4": "the arena, in the window chosen for it"},
            "meta": {"arena": "int", "crop_px": "[x, y, w, h] in the raw video",
                     "t_offset_s": "raw-video time of the first frame", "start_frame": "raw-video frame index of it",
                     "fps": "float", "n_frames": "int, counted", "width": "int", "height": "int"},
        },
    },
    TRAJECTORIES: {
        "name": "Positions and features of every fly in one arena, frame by frame",
        "spec": {"file": "Parquet, one row per fly per frame",
                 "required_columns": ["frame", "t_s", "fly", "x_mm", "y_mm", "heading_rad", "x_px", "y_px"],
                 "time": "frame and t_s are raw-video frame index and seconds",
                 "space": "mm from the crop's top-left corner; arena centre and scale in meta.calibration",
                 "meta": {"n_flies": "int", "n_frames": "int", "n_flags": "potential identity swaps",
                          "flags_per_min": "float", "flags": "[{flies, frames, ambiguity}]"}},
    },
    FT_TRACK: {"name": "FlyTracker -track.mat (native; the visualizer edits it)", "spec": {}},
    FT_FEAT: {"name": "FlyTracker -feat.mat (native)", "spec": {}},
    FT_CALIBRATION: {"name": "FlyTracker calibration (template, or per video when the arena is re-detected)", "spec": {}},
}


def asset_type(key: tuple[str, int]) -> AssetType:
    code, version = key
    t, _ = AssetType.objects.get_or_create(code=code, version=version, defaults=SPECS[key])
    return t


def validate_raw_video(meta: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings). Errors stop the ingest; warnings are stored on the asset."""
    errors, warnings = [], []
    for field in ("duration_s", "fps", "n_frames", "width", "height"):
        if not meta.get(field) or meta[field] <= 0:
            errors.append(f"{field} missing or not positive")
    if errors:
        return errors, warnings
    expected = meta["duration_s"] * meta["fps"]
    if abs(meta["n_frames"] - expected) > max(2, 0.001 * expected):
        warnings.append(f"The file declares {meta['fps']:g} fps, but {meta['n_frames']} frames in "
                        f"{meta['duration_s']:.2f} s is {meta['measured_fps']:.3f} fps. Frames may have been "
                        f"dropped, or the declared rate is wrong.")
    return errors, warnings


def validate_crop_video(meta: dict, expected_frames: int | None, fps: float | None) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    for field in ("n_frames", "width", "height", "fps"):
        if not meta.get(field) or meta[field] <= 0:
            errors.append(f"{field} missing or not positive")
    if errors:
        return errors, warnings
    if fps and abs(meta["fps"] - fps) > 0.01:
        warnings.append(f"Frame rate {meta['fps']:g} differs from the raw video ({fps:g}).")
    if expected_frames and abs(meta["n_frames"] - expected_frames) > 2:
        warnings.append(f"{meta['n_frames']} frames, expected {expected_frames} for the arena window.")
    return errors, warnings


TRAJECTORY_COLUMNS = SPECS[TRAJECTORIES]["spec"]["required_columns"]


def validate_trajectories(path, fps: float | None, expected_flies: int | None) -> tuple[list[str], list[str], dict]:
    """(errors, warnings, meta additions) for a trajectories/1 Parquet file."""
    import pandas as pd
    errors, warnings = [], []
    try:
        df = pd.read_parquet(path, columns=None)
    except Exception as e:  # noqa: BLE001
        return [f"Not a readable Parquet file: {e}"], [], {}
    missing = [c for c in TRAJECTORY_COLUMNS if c not in df.columns]
    if missing:
        return [f"Missing columns: {', '.join(missing)}"], [], {}
    if df.empty:
        return ["No rows."], [], {}
    if df.duplicated(["frame", "fly"]).any():
        errors.append("Duplicate (frame, fly) rows.")
    per_fly = df.groupby("fly")["frame"].agg(["count", "min", "max"])
    if per_fly["count"].nunique() != 1:
        errors.append("Flies have different numbers of frames.")
    if fps and ((df["t_s"] - df["frame"] / fps).abs() > 0.5 / fps).any():
        errors.append("t_s does not match frame / fps.")
    n_flies = len(per_fly)
    if expected_flies and n_flies != expected_flies:
        warnings.append(f"Tracked {n_flies} flies, expected {expected_flies}.")
    missing_pos = float(df["x_mm"].isna().mean())
    if missing_pos > 0.05:
        warnings.append(f"Position missing in {missing_pos:.1%} of rows.")
    return errors, warnings, {"n_rows": int(len(df)), "n_flies_checked": n_flies,
                              "frame_range": [int(per_fly["min"].min()), int(per_fly["max"].max())]}
