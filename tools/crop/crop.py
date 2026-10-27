"""crop@1.0.0 - cut arenas out of a raw video along the layout grid.

    python crop.py --inputs inputs.json --params params.json --out OUT_DIR

Writes OUT_DIR/A01.mp4 ... and OUT_DIR/outputs.json. Knows nothing about the database:
everything it needs is in the two JSON files, written by the worker.
Times stay in raw-video time: each output records the time and frame index of its first frame.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        exe = shutil.which("ffmpeg")
        if not exe:
            sys.exit("ffmpeg not found: pip install imageio-ffmpeg, or put ffmpeg on PATH")
        return exe


def build_command(src: str, arenas: list, out: Path, fps: float, enc: dict) -> list:
    graph, outputs = [f"[0:v]split={len(arenas)}" + "".join(f"[s{i}]" for i in range(len(arenas)))], []
    for i, a in enumerate(arenas):
        x, y, w, h = a["crop_px"]
        start, end = a["window_s"]
        chain = f"[s{i}]crop={w}:{h}:{x}:{y}"
        if start is not None or end is not None:
            trim = [f"start_frame={round((start or 0) * fps)}"] + ([f"end_frame={round(end * fps)}"] if end else [])
            chain += f",trim={':'.join(trim)},setpts=PTS-STARTPTS"
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
    cmd = build_command(video["path"], arenas, out, fps, params["encoder"])
    print("running:", " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    subprocess.run(cmd, check=True)

    outputs = []
    for a in arenas:
        start = a["window_s"][0] or 0
        outputs.append({"name": f"A{a['index']:02d}", "type": "crop_video/1", "file": f"A{a['index']:02d}.mp4",
                        "arena": a["index"],
                        "meta": {"arena": a["index"], "crop_px": a["crop_px"], "t_offset_s": start,
                                 "start_frame": round(start * fps), "source_sha256": video["sha256"]}})
    (out / "outputs.json").write_text(json.dumps({"outputs": outputs}, indent=2))
    print(f"cropped {len(outputs)} arenas", flush=True)


if __name__ == "__main__":
    main()
