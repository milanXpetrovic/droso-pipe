"""Data formats (asset types) known to the system, with their validators.

When the worker and external tools arrive, this moves into the shared contracts/ package;
until then the app is the only producer and consumer of these formats.
"""

from .models import AssetType

RAW_VIDEO = ("raw_video", 1)
CROP_VIDEO = ("crop_video", 1)

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
