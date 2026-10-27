"""Reading video files with the ffmpeg binary shipped in the imageio-ffmpeg wheel (no root needed)."""

import re
import subprocess
from pathlib import Path

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
VIDEO_STREAM = re.compile(r"Stream #\S+.*?: Video: (?P<codec>\w+)(?P<rest>.*)")
SIZE = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
FPS = re.compile(r"([\d.]+) fps")
TBR = re.compile(r"([\d.]+)k? tbr")


def probe(path: Path) -> dict:
    """Duration, fps, resolution and codec from the container header (no decoding, ~0.1 s).
    The fps is what the file declares; count_frames gives the real number of frames."""
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30)
    info = {"duration_s": None, "fps": None, "width": None, "height": None, "codec": ""}
    if m := DURATION.search(p.stderr):
        info["duration_s"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    if v := VIDEO_STREAM.search(p.stderr):
        info["codec"] = v["codec"]
        if size := SIZE.search(v["rest"]):
            info["width"], info["height"] = int(size[1]), int(size[2])
        if fps := FPS.search(v["rest"]) or TBR.search(v["rest"]):
            info["fps"] = float(fps[1])
    return info


def count_frames(path: Path) -> int | None:
    """Count video frames without decoding: framecrc writes one line per packet of the video stream.
    Reads the whole file once; output is streamed, not stored."""
    proc = subprocess.Popen([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path),
                             "-map", "0:v:0", "-c", "copy", "-f", "framecrc", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    n = sum(1 for line in proc.stdout if not line.startswith(b"#"))
    proc.stdout.close()
    return n if proc.wait(timeout=60) == 0 and n > 0 else None
