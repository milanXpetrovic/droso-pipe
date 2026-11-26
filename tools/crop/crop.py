"""crop@1.1.0 - cut each arena out of a raw video: the bright arena in its grid cell, plus a margin.

    python crop.py --inputs inputs.json --params params.json --out OUT_DIR

Writes OUT_DIR/A01.mp4 ... and OUT_DIR/outputs.json. Knows nothing about the database:
everything it needs is in the two JSON files, written by the worker.

Arenas are white on a black background. The layout grid (rows x cols) says roughly where each one is;
the arena itself is found on a background image (median of a few frames, so flies disappear) as the
bright region around its cell, and cut out with params["margin"] of its size on every side. If no arena
is found the whole grid cell is cut, with a warning: the video is never lost for that.
Times stay in raw-video time: each output records the time and frame index of its first frame.
params["fps"] is the real frame rate (checked at ingest); outputs are written at that rate, frame for frame.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

N_BACKGROUND_FRAMES = 9
ANALYSIS_WIDTH = 1000          # background image is analysed at about this width


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        exe = shutil.which("ffmpeg")
        if not exe:
            sys.exit("ffmpeg not found: pip install imageio-ffmpeg, or put ffmpeg on PATH")
        return exe


def even(n: float) -> int:
    return int(n) // 2 * 2


# --- finding the arena -------------------------------------------------------------------------

def background(src: str, size: tuple, duration_s: float, factor: int):
    """Median of frames spread over the file (seek times in the file's own time): an image without flies."""
    import numpy as np
    w, h = size[0] // factor, size[1] // factor
    frames = []
    for k in range(N_BACKGROUND_FRAMES):
        t = duration_s * (k + 0.5) / N_BACKGROUND_FRAMES
        raw = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1",
                              "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        if len(raw) == w * h:
            frames.append(np.frombuffer(raw, np.uint8).reshape(h, w))
    if not frames:
        raise RuntimeError("could not read frames for the background image")
    return np.median(np.stack(frames), axis=0)


def otsu(values) -> float:
    import numpy as np
    hist = np.bincount(values.astype(np.uint8).ravel(), minlength=256).astype(float)
    p = hist / hist.sum()
    omega, mu = np.cumsum(p), np.cumsum(p * np.arange(256))
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu[-1] * omega - mu) ** 2 / (omega * (1 - omega))
    return float(np.nanargmax(between))


def bright_run(profile, seed_from: int, seed_to: int, max_gap: int):
    """The stretch of a profile around its peak inside [seed_from, seed_to), bridging gaps (a divider wall)."""
    import numpy as np
    seed = seed_from + int(np.argmax(profile[seed_from:seed_to]))
    floor = max(2.0, 0.02 * profile[seed])
    if profile[seed] < floor * 5:
        return None
    ends = []
    for step in (-1, 1):
        i, last = seed, seed
        while 0 <= i + step < len(profile) and abs(i + step - last) <= max_gap:
            i += step
            if profile[i] >= floor:
                last = i
        ends.append(last)
    return ends[0], ends[1] + 1


def find_arena(bg, cell: tuple):
    """(x, y, w, h) of the bright arena around a grid cell of the background image, or None."""
    x, y, w, h = cell
    H, W = bg.shape
    ex0, ex1 = max(0, x - w // 2), min(W, x + w + w // 2)      # look half a cell beyond: arenas are not centred
    ey0, ey1 = max(0, y - h // 2), min(H, y + h + h // 2)
    cell_img = bg[y:y + h, x:x + w]
    if cell_img.max() - cell_img.min() < 20:
        return None                                              # no contrast: nothing to find
    mask = bg > otsu(cell_img)
    gap = max(2, min(w, h) // 30)
    cols = bright_run(mask[y:y + h, ex0:ex1].sum(axis=0), x - ex0, x + w - ex0, gap)
    if cols is None:
        return None
    xa, xb = ex0 + cols[0], ex0 + cols[1]
    rows = bright_run(mask[ey0:ey1, xa:xb].sum(axis=1), y - ey0, y + h - ey0, gap)
    if rows is None:
        return None
    ya, yb = ey0 + rows[0], ey0 + rows[1]
    cols = bright_run(mask[ya:yb, ex0:ex1].sum(axis=0), x - ex0, x + w - ex0, gap)   # again, on the arena's rows
    if cols is None:
        return None
    xa, xb = ex0 + cols[0], ex0 + cols[1]
    aw, ah = xb - xa, yb - ya
    if not 0.75 <= aw / ah <= 1.33 or min(aw, ah) < 0.25 * min(w, h) or mask[ya:yb, xa:xb].mean() < 0.5:
        return None                                              # not a round/square bright arena
    return xa, ya, aw, ah


def arena_boxes(src: str, arenas: list, size: tuple, duration_s: float, margin: float) -> list:
    """Sets a["crop_px"] (arena plus margin, or its grid cell) and a["warnings"] on every arena."""
    W, H = size
    factor = max(1, W // ANALYSIS_WIDTH)
    try:
        bg = background(src, size, duration_s, factor)
    except Exception as e:  # noqa: BLE001 - fall back to the grid, never lose the video
        print(f"background image failed ({e}): cutting the grid cells", flush=True)
        bg = None
    for a in arenas:
        a["crop_px"], a["warnings"] = list(a["cell_px"]), []
        found = find_arena(bg, tuple(v // factor for v in a["cell_px"])) if bg is not None else None
        if found is None:
            a["warnings"].append("Arena not found in its grid cell: the whole cell was cut.")
            print(f"A{a['index']:02d}: arena not found, cutting cell {a['cell_px']}", flush=True)
            continue
        xa, ya, aw, ah = (v * factor for v in found)
        cx, cy, mw, mh = xa + aw / 2, ya + ah / 2, aw * (1 + 2 * margin), ah * (1 + 2 * margin)
        x0, y0 = even(max(0, cx - mw / 2)), even(max(0, cy - mh / 2))
        a["crop_px"] = [x0, y0, even(min(W, cx + mw / 2) - x0), even(min(H, cy + mh / 2) - y0)]
        a["arena_px"] = [xa, ya, aw, ah]
        print(f"A{a['index']:02d}: arena {aw}x{ah} px at {xa},{ya} -> crop {a['crop_px']}", flush=True)


# --- cutting -----------------------------------------------------------------------------------

def build_command(src: str, arenas: list, out: Path, fps: float, enc: dict) -> list:
    graph, outputs = [f"[0:v]split={len(arenas)}" + "".join(f"[s{i}]" for i in range(len(arenas)))], []
    for i, a in enumerate(arenas):
        x, y, w, h = a["crop_px"]
        start, end = a["window_s"]
        chain = f"[s{i}]crop={w}:{h}:{x}:{y}"
        if start is not None or end is not None:
            trim = [f"start_frame={round((start or 0) * fps)}"] + ([f"end_frame={round(end * fps)}"] if end else [])
            chain += f",trim={':'.join(trim)}"
        # Timestamps from the frame number at the real rate: the file's own rate may be wrong (camera free-running
        # faster than declared), and "-r" alone would then duplicate or drop frames instead of relabelling them.
        chain += f",setpts=N/({fps}*TB)"
        graph.append(f"{chain}[o{i}]")
        outputs += ["-map", f"[o{i}]", "-c:v", enc["codec"], "-pix_fmt", enc["pix_fmt"], "-r", f"{fps}"]
        if enc["codec"] == "libx264":
            outputs += ["-crf", str(enc["crf"]), "-preset", enc["preset"]]
        outputs += ["-an", "-y", str(out / f"A{a['index']:02d}.mp4")]
    return [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-stats", "-i", src,
            "-filter_complex", ";".join(graph), *outputs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    inputs = json.loads(Path(args.inputs).read_text())
    params = json.loads(Path(args.params).read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    video = next(i for i in inputs if i["type"] == "raw_video/1")
    arenas, fps = params["arenas"], float(params["fps"])
    if not arenas:
        sys.exit("No valid arenas to crop.")
    arena_boxes(video["path"], arenas, tuple(params["source_size"]), float(params["duration_s"]), float(params["margin"]))
    cmd = build_command(video["path"], arenas, out, fps, params["encoder"])
    print("running:", " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    subprocess.run(cmd, check=True)

    outputs = []
    for a in arenas:
        start = a["window_s"][0] or 0
        meta = {"arena": a["index"], "crop_px": a["crop_px"], "cell_px": a["cell_px"], "arena_px": a.get("arena_px"),
                "t_offset_s": start, "start_frame": round(start * fps), "source_sha256": video["sha256"]}
        if a["warnings"]:
            meta["tool_warnings"] = a["warnings"]
        outputs.append({"name": f"A{a['index']:02d}", "type": "crop_video/1", "file": f"A{a['index']:02d}.mp4",
                        "arena": a["index"], "meta": meta})
    (out / "outputs.json").write_text(json.dumps({"outputs": outputs}, indent=2))
    print(f"cropped {len(outputs)} arenas", flush=True)


if __name__ == "__main__":
    main()
