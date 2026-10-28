"""FlyTracker output -> trajectories/1 (one Parquet table per arena).

Reads -track.mat and -feat.mat (MATLAB v5 or v7.3) and writes one row per fly per frame:
    frame, t_s, fly, x_mm, y_mm, heading_rad, ...   (see COLUMNS)
frame and t_s are in RAW-video time (the crop's start_frame is added), so events, windows and
other tools line up. Positions are in mm from the crop's top-left corner; x_px, y_px are kept
for drawing on the video. FlyTracker's flags (potential identity swaps) become a summary.
"""

from pathlib import Path

import numpy as np

TRACK_COLUMNS = {                          # FlyTracker name -> (our name, unit in the file)
    "pos x": ("x_mm", "px"), "pos y": ("y_mm", "px"), "ori": ("heading_rad", None),
    "major axis len": ("major_mm", "px"), "minor axis len": ("minor_mm", "px"),
    "body area": ("body_area_mm2", "px2"), "fg area": ("fg_area_mm2", "px2"),
    "img contrast": ("img_contrast", None), "min fg dist": ("min_fg_dist_mm", "px"),
    "wing l ang": ("wing_l_ang_rad", None), "wing r ang": ("wing_r_ang_rad", None),
    "wing l len": ("wing_l_len_mm", "px"), "wing r len": ("wing_r_len_mm", "px"),
}
FEAT_COLUMNS = {                           # already in physical units in -feat.mat
    "vel": "vel_mm_s", "ang_vel": "ang_vel_rad_s", "min_wing_ang": "min_wing_ang_rad",
    "max_wing_ang": "max_wing_ang_rad", "mean_wing_length": "mean_wing_length_mm",
    "axis_ratio": "axis_ratio", "fg_body_ratio": "fg_body_ratio", "contrast": "contrast",
    "dist_to_wall": "dist_to_wall_mm",
}
REQUIRED = ["frame", "t_s", "fly", "x_mm", "y_mm", "heading_rad", "x_px", "y_px"]


def _h5_string(h, ref) -> str:
    arr = h[ref][()]
    return "".join(chr(c) for c in arr.flatten()) if arr.dtype != object else ""


def load_struct(path: Path, name: str) -> dict:
    """{'data': flies x frames x features, 'names': [...], 'flags': n x 6 (track only)}."""
    try:
        import h5py
    except ImportError:                                        # only needed for MATLAB v7.3 files
        h5py = None
    try:
        if h5py is None:
            raise OSError
        with h5py.File(path, "r") as h:                       # MATLAB v7.3
            g = h[name]
            out = {"data": np.array(g["data"]).T,
                   "names": [_h5_string(h, r) for r in np.array(g["names"]).flatten()]}
            if "flags" in g:
                f = g["flags"]
                out["flags"] = np.zeros((0, 6)) if f.attrs.get("MATLAB_empty") else np.array(f).T
            return out
    except OSError:
        pass
    import scipy.io as sio                                     # MATLAB v5
    s = sio.loadmat(path, squeeze_me=False, struct_as_record=False)[name][0, 0]
    out = {"data": np.asarray(s.data, dtype=float),
           "names": [str(np.asarray(n).flatten()[0]) for n in s.names.flatten()]}
    if "flags" in s._fieldnames:
        flags = np.asarray(s.flags, dtype=float)
        out["flags"] = flags.reshape(-1, 6) if flags.size else np.zeros((0, 6))
    return out


def calibration_info(path: Path) -> dict:
    """Works for circle calibrations (centroids, r) and full-frame ones (no circle at all)."""
    import scipy.io as sio
    c = sio.loadmat(path, squeeze_me=True, struct_as_record=False)["calib"]
    fields = set(c._fieldnames)
    centre = np.atleast_1d(c.centroids).astype(float).tolist() if "centroids" in fields else []
    r = getattr(c, "r", None)
    return {"ppm": float(c.PPM), "arena_r_px": float(r) if r is not None and np.size(r) else None,
            "arena_centre_px": [centre[1], centre[0]] if len(centre) == 2 else None,   # FlyTracker stores (y, x)
            "fps": float(c.FPS), "n_flies": int(c.n_flies), "auto_detect": int(c.auto_detect)}


def to_table(track_file: Path, feat_file: Path | None, ppm: float, fps: float, start_frame: int = 0):
    """Returns (pandas.DataFrame, summary dict)."""
    import pandas as pd
    trk = load_struct(track_file, "trk")
    n_flies, n_frames, _ = trk["data"].shape
    frames = start_frame + np.arange(n_frames)
    cols = {"frame": np.tile(frames, n_flies).astype("int64"),
            "fly": np.repeat(np.arange(1, n_flies + 1), n_frames).astype("int16")}
    cols["t_s"] = cols["frame"] / fps
    index = {n: i for i, n in enumerate(trk["names"])}
    for name, (col, unit) in TRACK_COLUMNS.items():
        if name not in index:
            continue
        v = trk["data"][:, :, index[name]].reshape(-1)
        if name in ("pos x", "pos y"):
            cols["x_px" if name == "pos x" else "y_px"] = v.astype("float32")
        cols[col] = v / ppm if unit == "px" else v / ppm ** 2 if unit == "px2" else v
    if feat_file is not None and Path(feat_file).exists():
        feat = load_struct(feat_file, "feat")
        findex = {n: i for i, n in enumerate(feat["names"])}
        for name, col in FEAT_COLUMNS.items():
            if name in findex:
                v = feat["data"][:, :, findex[name]].reshape(-1)
                cols[col] = np.where(np.isfinite(v), v, np.nan)
    df = pd.DataFrame(cols)
    df = df[["frame", "t_s", "fly"] + [c for c in df.columns if c not in ("frame", "t_s", "fly")]]
    floats = [c for c in df.columns if c not in ("frame", "t_s", "fly")]
    df[floats] = df[floats].astype("float32")              # ~0.01 px precision, half the size

    flags = trk.get("flags", np.zeros((0, 6)))
    minutes = n_frames / fps / 60
    summary = {
        "n_flies": int(n_flies), "n_frames": int(n_frames), "ppm": ppm, "fps": fps, "start_frame": start_frame,
        "n_flags": int(len(flags)), "flags_per_min": round(len(flags) / minutes, 3) if minutes else None,
        "flags": [{"flies": [int(a), int(b)], "frames": [start_frame + int(s) - 1, start_frame + int(e) - 1],
                   "ambiguity": round(float(amb), 4)} for a, b, s, e, amb, *_ in flags[:1000]],
        "missing_position_fraction": round(float(np.isnan(df["x_mm"]).mean()), 5),
    }
    return df, summary


def write_parquet(df, path: Path):
    df.to_parquet(path, index=False, compression="zstd")
