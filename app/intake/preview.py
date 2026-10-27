"""Single frames from a video, for the operator to see the arenas.

Uses the ffmpeg binary shipped with the imageio-ffmpeg wheel, so it works without root
on milky and inside the Docker image alike. Seeking with -ss before -i jumps to the nearest
keyframe, so even a 30 GB file gives a frame in about a second.
"""

import subprocess
from pathlib import Path

from django.conf import settings

from core.video import FFMPEG, count_frames, probe  # noqa: F401  (re-exported for intake)


def video_path(rel_path: str) -> Path:
    from core.storage import resolve
    return resolve(rel_path)


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
