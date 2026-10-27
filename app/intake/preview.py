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


def video_path(rel_path: str) -> Path:
    root = Path(settings.VIDEOS_ROOT).resolve()
    path = (root / rel_path).resolve()
    if root not in path.parents:
        raise ValueError("Path outside the videos root.")
    return path


def duration_s(path: Path) -> float | None:
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30)
    m = DURATION.search(p.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


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
