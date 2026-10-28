"""flytracker@1.0.0 - track one arena video with FlyTracker and convert the result.

    python track.py --matlab PATH_TO_matlab --flytracker PATH_TO_FlyTracker \
                    --inputs inputs.json --params params.json --out OUT_DIR

Inputs: one crop_video/1 and one flytracker_calibration/1 (the template).
Params (filled in by the server): mode, n_flies, arena_r_mm, options, fps, start_frame, expected_ppm.
Writes A01-track.mat, A01-feat.mat, A01-calibration.mat (if the arena was re-detected),
A01-trajectories.parquet and outputs.json.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from convert import calibration_info, to_table, write_parquet  # noqa: E402

PPM_TOLERANCE = 0.05


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matlab", default="matlab")
    ap.add_argument("--flytracker", required=True, help="folder with FlyTracker's modpath.m")
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    inputs = json.loads(Path(args.inputs).read_text())
    params = json.loads(Path(args.params).read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    video = next(i for i in inputs if i["type"] == "crop_video/1")
    template = next(i for i in inputs if i["type"] == "flytracker_calibration/1")
    name = Path(video["rel_path"]).stem                       # A01
    work = Path(tempfile.mkdtemp(prefix="flytracker-"))
    try:
        # FlyTracker names its outputs after the video file, so give it a local copy named A01.<ext>
        local_video = work / f"{name}{Path(video['path']).suffix}"
        shutil.copyfile(video["path"], local_video)
        local_calib = work / "template-calibration.mat"
        shutil.copyfile(template["path"], local_calib)
        job = {"flytracker_dir": args.flytracker, "video": str(local_video), "calibration": str(local_calib),
               "out_dir": str(work / "result"), "options": params["options"]}
        job_file = work / "job.json"
        job_file.write_text(json.dumps(job, indent=2))
        here = str(Path(__file__).parent).replace("'", "''")
        cmd = [args.matlab, "-batch", f"addpath('{here}'); run_tracker('{job_file.as_posix()}')"]
        print("running:", " ".join(cmd), flush=True)
        subprocess.run(cmd, check=True)

        result = work / "result"
        track, feat = result / f"{name}-track.mat", result / f"{name}-feat.mat"
        if not track.exists():
            sys.exit(f"FlyTracker produced no {track.name}")
        out_calib = result / f"{name}-calibration.mat"
        calib = calibration_info(out_calib if out_calib.exists() else local_calib)

        warnings = []
        expected = params.get("expected_ppm")
        if expected and abs(calib["ppm"] - expected) / expected > PPM_TOLERANCE:
            warnings.append(f"Scale {calib['ppm']:.2f} px/mm differs from the expected {expected:.2f} by more than "
                            f"{PPM_TOLERANCE:.0%}: the arena may have been detected wrongly.")
        df, summary = to_table(track, feat, ppm=calib["ppm"], fps=params["fps"], start_frame=params["start_frame"])
        if params.get("n_flies") and summary["n_flies"] != params["n_flies"]:
            warnings.append(f"Tracked {summary['n_flies']} flies, expected {params['n_flies']}.")
        write_parquet(df, out / f"{name}-trajectories.parquet")
        for f in (track, feat, out_calib):
            if f.exists():
                shutil.copyfile(f, out / f.name)

        arena = params["arena"]
        meta = {**summary, "calibration": calib, "mode": params["mode"], "tool_warnings": warnings}
        outputs = [{"name": f"{name}-trajectories", "type": "trajectories/1", "file": f"{name}-trajectories.parquet",
                    "arena": arena, "meta": meta},
                   {"name": f"{name}-track", "type": "flytracker_track/1", "file": track.name, "arena": arena,
                    "meta": {"n_flags": summary["n_flags"]}}]
        if feat.exists():
            outputs.append({"name": f"{name}-feat", "type": "flytracker_feat/1", "file": feat.name, "arena": arena,
                            "meta": {}})
        if out_calib.exists():
            outputs.append({"name": f"{name}-calibration", "type": "flytracker_calibration/1", "file": out_calib.name,
                            "arena": arena, "meta": calib})
        (out / "outputs.json").write_text(json.dumps({"outputs": outputs}, indent=2))
        print(f"tracked {summary['n_flies']} flies, {summary['n_frames']} frames, {summary['n_flags']} flags", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
