"""Tools the system knows how to schedule, with the parameters each run gets.

Parameters are resolved when a worker claims the run, so changes the operator makes
(windows, excluded arenas) before processing are respected; the run then stores them.
"""

from domain.models import Arena

from .formats import video_fps
from .models import Tool

CROP = {"code": "crop", "version": "1.1.0",
        "description": "Cut every valid arena out of the raw video: the bright arena found in its grid cell plus a "
                       "margin (the whole cell if none is found), within its window.",
        "spec": {"inputs": {"video": "raw_video/1"}, "outputs": {"A<nn>": "crop_video/1"},
                 "params": {"arenas": "[{index, code, cell_px, window_s}]", "margin": "fraction of the arena size",
                            "encoder": "ffmpeg settings"}}}

FLYTRACKER = {"code": "flytracker", "version": "1.0.0",
              "description": "Track one arena with FlyTracker (headless) and convert the result to trajectories/1.",
              "spec": {"inputs": {"video": "crop_video/1", "template": "flytracker_calibration/1"},
                       "outputs": {"A<nn>-trajectories": "trajectories/1", "A<nn>-track": "flytracker_track/1",
                                   "A<nn>-feat": "flytracker_feat/1"}}}

ENCODER = {"codec": "libx264", "crf": 18, "preset": "fast", "pix_fmt": "yuv420p"}
CROP_MARGIN = 0.05                 # of the arena's width/height, on every side


def tool(defn: dict) -> Tool:
    t, _ = Tool.objects.get_or_create(code=defn["code"], version=defn["version"],
                                      defaults={"description": defn["description"], "spec": defn["spec"]})
    return t


def even(n: float) -> int:
    return int(n) // 2 * 2


def crop_params(run) -> dict:
    """The frame is divided into rows x cols equal cells, A01 top-left, row by row; the tool finds the arena in each."""
    raw = run.inputs.get(type__code="raw_video")
    rec, layout = run.recording, run.recording.layout
    W, H = raw.meta["width"], raw.meta["height"]
    cw, ch = even(W / layout.cols), even(H / layout.rows)
    arenas = []
    for a in rec.arenas.filter(status=Arena.Status.OK).order_by("index"):
        r, c = layout.position(a.index)
        arenas.append({"index": a.index, "code": a.code,
                       "cell_px": [even((c - 1) * W / layout.cols), even((r - 1) * H / layout.rows), cw, ch],
                       "window_s": [a.window_start_s, a.window_end_s]})
    return {"layout": layout.code, "rows": layout.rows, "cols": layout.cols, "source_size": [W, H],
            "fps": video_fps(raw.meta), "n_frames": raw.meta["n_frames"], "duration_s": raw.meta["duration_s"],
            "arenas": arenas, "margin": CROP_MARGIN, "encoder": ENCODER}


def flytracker_params(run) -> dict:
    """Template + this video + what the database knows: number of flies, arena size, frame rate."""
    from .models import TrackerTemplate
    crop = run.inputs.get(type__code="crop_video")
    template = TrackerTemplate.objects.select_related("asset", "layout").get(asset__in=run.inputs.all())
    arena, layout = crop.arena, run.recording.layout
    raw = run.recording.assets.filter(type__code="raw_video").first()
    n_flies = sum(g.n_flies or 0 for g in arena.fly_groups.all()) or None
    # num_cores is the worker's business (its own machine); chunking follows video length only (granularity),
    # so the result does not depend on which worker tracked it.
    options = {"granularity": 10000, "isdisplay": False,
               "save_xls": False, "save_JAABA": False, "save_seg": False, "n_flies": n_flies,
               "min_fractional_arena_size": template.min_fractional_arena_size,
               "max_fractional_arena_size": template.max_fractional_arena_size}
    expected_ppm = template.asset.meta.get("ppm")
    if template.mode == TrackerTemplate.Mode.CIRCLE:
        options["force_arena_calib"] = True
        if layout.arena_diameter_mm:
            options["arena_r_mm"] = layout.arena_diameter_mm / 2
            # FlyTracker takes the scale from the arena it finds (radius / arena_r_mm), so that is what to expect,
            # not the calibrator's ruler, which a person drew by hand.
            if template.asset.meta.get("arena_r_px"):
                expected_ppm = template.asset.meta["arena_r_px"] / options["arena_r_mm"]
    return {"mode": template.mode, "template": template.name, "arena": arena.index, "n_flies": n_flies,
            "fps": video_fps(raw.meta) if raw else crop.meta.get("fps"), "start_frame": crop.meta.get("start_frame", 0),
            "expected_ppm": expected_ppm, "options": options}


PARAMS = {"crop": crop_params, "flytracker": flytracker_params}


def resolve_params(run) -> dict:
    return PARAMS[run.tool.code](run) if run.tool.code in PARAMS else run.params
