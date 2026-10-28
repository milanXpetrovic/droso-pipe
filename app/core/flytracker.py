"""Reading FlyTracker calibration files on the server (template upload and checks)."""

from pathlib import Path

import numpy as np


def calibration_info(path: Path) -> dict:
    """Works for circle calibrations (centroids, r) and full-frame ones (no circle at all)."""
    import scipy.io as sio
    c = sio.loadmat(path, squeeze_me=True, struct_as_record=False)["calib"]
    fields = set(c._fieldnames)
    centre = np.atleast_1d(c.centroids).astype(float).tolist() if "centroids" in fields else []
    r = getattr(c, "r", None)
    mask = np.asarray(c.mask) if "mask" in fields else np.zeros((0, 0))
    return {"ppm": float(c.PPM), "arena_r_px": float(r) if r is not None and np.size(r) else None,
            "arena_centre_px": [centre[1], centre[0]] if len(centre) == 2 else None,   # stored as (y, x)
            "image_size_px": [int(mask.shape[1]), int(mask.shape[0])] if mask.ndim == 2 and mask.size else None,
            "roi_type": int(c.roi_type) if "roi_type" in fields else None,
            "fps": float(c.FPS), "n_flies": int(c.n_flies), "auto_detect": int(c.auto_detect)}


def template_warnings(info: dict, mode: str, arena_diameter_mm: float | None) -> list[str]:
    """Problems worth knowing before a template is used for hundreds of videos."""
    warnings = []
    r, centre, size = info.get("arena_r_px"), info.get("arena_centre_px"), info.get("image_size_px")
    if mode == "circle" and r is None:
        warnings.append("This calibration has no circular arena, so it cannot be used in circle mode. "
                        "Use full-frame mode, or calibrate with a circular arena.")
    if mode == "circle":
        if r and centre and size:
            x, y = centre
            if x - r < -2 or y - r < -2 or x + r > size[0] + 2 or y + r > size[1] + 2:
                warnings.append(f"The arena circle (radius {r:.0f} px at {x:.0f}, {y:.0f}) extends beyond the "
                                f"{size[0]}x{size[1]} image: the calibrator probably found an outer border, not the arena.")
        if r and arena_diameter_mm:
            implied = 2 * r / info["ppm"]
            if abs(implied - arena_diameter_mm) / arena_diameter_mm > 0.05:
                warnings.append(f"Radius {r:.0f} px at {info['ppm']:.2f} px/mm means a {implied:.1f} mm arena, but the "
                                f"layout says {arena_diameter_mm:g} mm.")
    return warnings
