"""Single frames from a video, for the operator to see the arenas.

Uses the ffmpeg binary shipped with the imageio-ffmpeg wheel, so it works without root
on milky and inside the Docker image alike. Seeking with -ss before -i jumps to the nearest
keyframe, so even a 30 GB file gives a frame in about a second.
"""

import re
import subprocess
from pathlib import Path

import imageio_ffmpeg
from django.conf import settings

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
VIDEO_STREAM = re.compile(r"Stream #\S+.*?: Video: (?P<codec>\w+)(?P<rest>.*)")
SIZE = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
FPS = re.compile(r"([\d.]+) fps")
TBR = re.compile(r"([\d.]+)k? tbr")


def video_path(rel_path: str) -> Path:
    root = Path(settings.VIDEOS_ROOT).resolve()
    path = (root / rel_path).resolve()
    if root not in path.parents:
        raise ValueError("Path outside the videos root.")
    return path


def probe(path: Path) -> dict:
    """Duration, fps, resolution and codec from the container header (no decoding, takes ~0.1 s).
    The fps is what the file declares; the frame count is checked later, at ingest."""
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30)
    info = {"duration_s": None, "fps": None, "width": None, "height": None, "codec": ""}
    m = DURATION.search(p.stderr)
    if m:
        info["duration_s"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    v = VIDEO_STREAM.search(p.stderr)
    if v:
        info["codec"] = v["codec"]
        if size := SIZE.search(v["rest"]):
            info["width"], info["height"] = int(size[1]), int(size[2])
        if fps := FPS.search(v["rest"]) or TBR.search(v["rest"]):
            info["fps"] = float(fps[1])
    return info


def duration_s(path: Path) -> float | None:
    return probe(path)["duration_s"]


def frame_jpeg(path: Path, t: float, cache_dir: Path, width: int = 1600) -> Path:
    """JPEG of the frame at t seconds, cached on disk."""
    out = cache_dir / f"{t:010.2f}.jpg"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.jpg")
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path),
                    "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", "-y", str(tmp)],
                   check=True, timeout=120)
    tmp.replace(out)
    return out
