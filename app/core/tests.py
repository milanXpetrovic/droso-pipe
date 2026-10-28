from django.test import TestCase

from .formats import CROP_VIDEO, RAW_VIDEO, asset_type, validate_raw_video


class FormatTests(TestCase):
    def test_asset_type_created_once(self):
        self.assertEqual(asset_type(RAW_VIDEO).pk, asset_type(RAW_VIDEO).pk)
        self.assertEqual(str(asset_type(RAW_VIDEO)), "raw_video/1")

    def meta(self, **kw):
        m = {"duration_s": 1800.0, "fps": 24.0, "n_frames": 43200, "width": 4024, "height": 3036}
        m.update(kw)
        m["measured_fps"] = m["n_frames"] / m["duration_s"]
        return m

    def test_consistent_video(self):
        self.assertEqual(validate_raw_video(self.meta()), ([], []))

    def test_dropped_frames_warn(self):
        errors, warnings = validate_raw_video(self.meta(n_frames=42000))
        self.assertEqual(errors, [])
        self.assertIn("dropped", warnings[0])

    def test_missing_fps_is_an_error(self):
        errors, _ = validate_raw_video(self.meta(fps=None))
        self.assertTrue(errors)


class AssetImmutabilityTests(TestCase):
    fixtures = ["catalogs", "lab"]

    def test_path_cannot_change(self):
        from .models import Asset, Run, Tool
        run = Run.objects.create(tool=Tool.objects.create(code="t", version="1"))
        a = Asset.objects.create(type=asset_type(RAW_VIDEO), rel_path="raw/x.mp4", sha256="0" * 64, size_bytes=1,
                                 produced_by=run)
        a.warnings = ["note"]                       # mutable fields may change
        a.save()
        a.rel_path = "raw/y.mp4"
        with self.assertRaises(ValueError):
            a.save()


# --- v0.8: API, worker, crop, commit ------------------------------------------------------------

import datetime as dt
import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from domain.models import ArenaLayout, Camera, Condition, Experiment, Recording, Rig

from .commit import commit_pending, requeue_stale
from .models import Asset, Run, Tool, Worker
from .rules import plan_runs
from .storage import sha256_of
from .video import FFMPEG, count_frames, probe

REPO = Path(settings.BASE_DIR).parent
T0 = dt.datetime(2026, 2, 3, 10, 15, 7, tzinfo=dt.timezone.utc)


class Store:
    """A recording with a real 640x480, 10 fps, 6 s raw video already ingested."""

    fixtures = ["catalogs", "lab"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("raw", "derived", "staging"):
            (self.root / d).mkdir()
        self.override = override_settings(VIDEOS_ROOT=self.root)
        self.override.enable()
        layout = ArenaLayout.objects.get(code="2x2")
        self.rec = Recording.objects.create(
            experiment=Experiment.objects.create(code="E", name="E"), rig=Rig.objects.get(code="R1"),
            camera=Camera.objects.get(code="1"), layout=layout, condition=Condition.objects.get(code="Control"),
            started_at=T0, n_flies=12)
        self.rec.populate_arenas()
        rel = f"raw/2026/02/{self.rec.code}/video.mp4"
        path = self.root / rel
        path.parent.mkdir(parents=True)
        subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=640x480:rate=10",
                        "-pix_fmt", "yuv420p", "-y", str(path)], check=True)
        ingest_run = Run.objects.create(tool=Tool.objects.create(code="ingest", version="1.0.0"),
                                        status=Run.Status.DONE, recording=self.rec)
        meta = {**probe(path), "n_frames": count_frames(path)}
        self.raw = Asset.objects.create(type=asset_type(RAW_VIDEO), rel_path=rel, sha256=sha256_of(path),
                                        size_bytes=path.stat().st_size, meta=meta, recording=self.rec,
                                        produced_by=ingest_run)
        self.worker = Worker(name="rig1-win", tools=["crop@1.0.0"])
        self.token = self.worker.new_token()
        self.worker.save()

    def tearDown(self):
        self.override.disable()
        self.tmp.cleanup()

    def api(self, path, body=None, token=None):
        return self.client.post(f"/api/{path}", json.dumps(body or {}), content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {token or self.token}")


class ApiTests(Store, TestCase):
    def test_token_required(self):
        self.assertEqual(self.client.post("/api/runs/claim", "{}", content_type="application/json").status_code, 401)
        self.assertEqual(self.api("runs/claim", {"tools": ["crop@1.0.0"]}, token="wrong").status_code, 401)

    def test_plan_and_claim_crop(self):
        self.assertEqual((plan_runs(), plan_runs()), (1, 0))                  # idempotent
        arena = self.rec.arenas.get(index=3)
        arena.status, arena.window_start_s, arena.window_end_s = "excluded", None, None
        arena.save()
        a2 = self.rec.arenas.get(index=2)
        a2.window_start_s, a2.window_end_s = 1.0, 4.0
        a2.save()
        spec = self.api("runs/claim", {"tools": ["crop@1.0.0"]}).json()
        self.assertEqual((spec["tool"], spec["recording"], spec["inputs"][0]["rel_path"]),
                         ("crop", self.rec.code, self.raw.rel_path))
        arenas = {a["index"]: a for a in spec["params"]["arenas"]}
        self.assertEqual(sorted(arenas), [1, 2, 4])                          # excluded arena 3 is not cropped
        self.assertEqual(arenas[1]["crop_px"], [0, 0, 320, 240])
        self.assertEqual(arenas[4]["crop_px"], [320, 240, 320, 240])
        self.assertEqual(arenas[2]["window_s"], [1.0, 4.0])
        self.assertEqual(self.api("runs/claim", {"tools": ["crop@1.0.0"]}).status_code, 204)   # nothing left

    def test_worker_only_gets_tools_it_is_allowed(self):
        plan_runs()
        self.worker.tools = []
        self.worker.save()
        self.assertEqual(self.api("runs/claim", {"tools": ["crop@1.0.0"]}).status_code, 204)

    def test_fail_retries_then_gives_up(self):
        plan_runs()
        for attempt in range(1, 4):
            spec = self.api("runs/claim", {"tools": ["crop@1.0.0"]}).json()
            status = self.api(f"runs/{spec['run']}/fail", {"error": "boom"}).json()["status"]
        self.assertEqual(status, "failed")
        self.assertEqual(Run.objects.get(tool__code="crop").attempts, 3)

    def test_silent_worker_is_requeued(self):
        plan_runs()
        spec = self.api("runs/claim", {"tools": ["crop@1.0.0"]}).json()
        Run.objects.filter(pk=spec["run"]).update(heartbeat_at=timezone.now() - dt.timedelta(minutes=30))
        self.assertEqual(requeue_stale(), 1)
        self.assertEqual(Run.objects.get(pk=spec["run"]).status, Run.Status.PENDING)

    def test_commit_rejects_tampered_files(self):
        plan_runs()
        spec = self.api("runs/claim", {"tools": ["crop@1.0.0"]}).json()
        staging = self.root / spec["staging"]
        staging.mkdir()
        (staging / "A01.mp4").write_bytes(b"not what was reported")
        self.api(f"runs/{spec['run']}/complete", {
            "files": [{"path": "A01.mp4", "sha256": "0" * 64, "size_bytes": 21}],
            "outputs": [{"type": "crop_video/1", "file": "A01.mp4", "arena": 1, "meta": {}}]})
        commit_pending()
        run = Run.objects.get(pk=spec["run"])
        self.assertEqual(run.status, Run.Status.FAILED)
        self.assertIn("checksum", run.error)
        self.assertFalse(Asset.objects.filter(type__code="crop_video").exists())
        self.assertFalse((self.root / "derived" / self.rec.code).exists() and
                         any((self.root / "derived" / self.rec.code).rglob("*.mp4")))


def load_worker():
    spec = importlib.util.spec_from_file_location("droso_worker", REPO / "worker" / "droso_worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkerEndToEnd(Store, LiveServerTestCase):
    """The real worker program, talking HTTP to a live server, running the real crop tool."""

    def test_crop_through_worker_and_commit(self):
        a2 = self.rec.arenas.get(index=2)
        a2.window_start_s, a2.window_end_s = 1.0, 4.0
        a2.save()
        a3 = self.rec.arenas.get(index=3)
        a3.status = "excluded"
        a3.save()
        plan_runs()

        w = load_worker()
        scratch = Path(self.tmp.name) / "scratch"
        cfg = {"name": "rig1-win", "server": self.live_server_url, "token": self.token, "scratch": str(scratch),
               "tools": {"crop@1.0.0": {"command": [sys.executable, str(REPO / "tools" / "crop" / "crop.py")]}},
               "storage": {"mode": "path", "root": str(self.root)}}
        self.assertTrue(w.work_once(cfg, w.Api(cfg["server"], cfg["token"]), w.Storage(cfg["storage"])))
        run = Run.objects.get(tool__code="crop")
        self.assertEqual(run.status, Run.Status.COMMITTING, run.error)

        self.assertEqual(commit_pending(), 1)
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.DONE, run.error)
        crops = {a.arena.index: a for a in Asset.objects.filter(type__code="crop_video").select_related("arena")}
        self.assertEqual(sorted(crops), [1, 2, 4])
        self.assertEqual([crops[i].meta["n_frames"] for i in (1, 2, 4)], [60, 30, 60])   # window 1-4 s = 30 frames
        self.assertEqual((crops[2].meta["t_offset_s"], crops[2].meta["start_frame"]), (1.0, 10))
        self.assertEqual((crops[1].meta["width"], crops[1].meta["height"]), (320, 240))
        self.assertTrue(all(not c.warnings for c in crops.values()), [c.warnings for c in crops.values()])
        f = self.root / crops[1].rel_path
        self.assertEqual(sha256_of(f), crops[1].sha256)
        self.assertFalse(f.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        self.assertTrue((f.parent / "manifest.json").exists())
        self.assertEqual(list(run.inputs.all()), [self.raw])                  # lineage: crop <- raw
        self.assertFalse((self.root / run.staging_rel).exists())              # staging cleaned
        self.assertFalse(scratch.joinpath(f"run-{run.pk}").exists())          # worker cleaned up


# --- v0.9: tracker templates, FlyTracker wrapper, trajectories ------------------------------------

import numpy as np
import scipy.io as sio
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import TrackerTemplate
from .rules import plan_tracking

FAKE_MATLAB = """#!{python}
# Stands in for MATLAB + FlyTracker in tests: writes outputs shaped like FlyTracker's.
import json, re, sys
from pathlib import Path
import numpy as np, scipy.io as sio
job = json.loads(Path(re.search(r"run_tracker\\('(.+?)'\\)", sys.argv[2]).group(1)).read_text())
out = Path(job["out_dir"]); out.mkdir(parents=True, exist_ok=True)
name = Path(job["video"]).stem
opts = job["options"]
n, frames = int(opts.get("n_flies") or 3), 20
names = ["pos x", "pos y", "ori", "major axis len", "minor axis len", "body area", "fg area", "img contrast", "min fg dist"]
data = np.zeros((n, frames, len(names)))
data[:, :, 0] = 100 + np.arange(n)[:, None] * 10      # x px
data[:, :, 1] = 200.0                                  # y px
data[:, :, 3] = 30.0                                   # major axis px
flags = np.array([[1, 2, 5, 8, 0.5, 6], [2, 3, 10, 12, 1.2, 11]], dtype=float)
sio.savemat(out / f"{{name}}-track.mat", {{"trk": {{"data": data, "names": np.array(names, dtype=object), "flags": flags}}}})
feat = np.ones((n, frames, 2))
sio.savemat(out / f"{{name}}-feat.mat", {{"feat": {{"data": feat, "names": np.array(["vel", "dist_to_wall"], dtype=object),
                                                 "units": np.array(["mm/s", "mm"], dtype=object)}}}})
if opts.get("force_arena_calib"):
    r_px = 300.0
    ppm = r_px / float(opts["arena_r_mm"]) * float(opts.get("ppm_error", 1.0))
    sio.savemat(out / f"{{name}}-calibration.mat", {{"calib": {{"PPM": ppm, "r": r_px, "centroids": np.array([240, 320]),
        "mask": np.zeros((480, 640)), "FPS": 10.0, "n_flies": n, "auto_detect": 1}}}})
print("fake FlyTracker done")
"""


def calibration_file(ppm=10.0, r=300.0, centre_yx=(350, 350), size=(700, 700)) -> bytes:
    path = Path(tempfile.mkdtemp()) / "calibration.mat"
    sio.savemat(path, {"calib": {"PPM": ppm, "r": r, "centroids": np.array(centre_yx), "mask": np.zeros(size[::-1]),
                                 "FPS": 10.0, "n_flies": 12, "auto_detect": 1}})
    return path.read_bytes()


class TemplateAdminTests(Store, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_superuser("admin", "", "x"))

    def upload(self, name, content, mode="circle"):
        return self.client.post("/admin/core/trackertemplate/add/", {
            "name": name, "layout": self.rec.layout.pk, "mode": mode, "active": "on",
            "min_fractional_arena_size": 0.9, "max_fractional_arena_size": 1.1, "num_cores": 8, "notes": "",
            "calibration_file": SimpleUploadedFile("calibration.mat", content)}, follow=True)

    def test_upload_stores_template_as_asset(self):
        layout = self.rec.layout
        layout.arena_diameter_mm = 60
        layout.save()
        self.upload("2x2-circle", calibration_file(ppm=10.0, r=300.0))          # 60 mm arena, inside the image
        t = TrackerTemplate.objects.get()
        self.assertEqual((t.asset.type.code, t.asset.meta["ppm"], t.warnings), ("flytracker_calibration", 10.0, []))
        f = self.root / t.asset.rel_path
        self.assertTrue(f.exists())
        self.assertEqual(sha256_of(f), t.asset.sha256)

    def test_outer_border_template_is_flagged(self):
        r = self.upload("border", calibration_file(ppm=12.82, r=798.0, centre_yx=(553, 833), size=(2012, 1518)))
        t = TrackerTemplate.objects.get()
        self.assertTrue(any("outer border" in w for w in t.warnings))
        self.assertContains(r, "outer border")

    def test_not_a_calibration_file(self):
        self.upload("bad", b"not a mat file")
        self.assertFalse(TrackerTemplate.objects.exists())


class TrackingEndToEnd(Store, LiveServerTestCase):
    """Crop assets -> planned tracking run -> real worker + real wrapper (fake MATLAB) -> commit -> trajectories."""

    def setUp(self):
        super().setUp()
        layout = self.rec.layout
        layout.arena_diameter_mm = 60
        layout.save()
        self.fake_matlab = Path(self.tmp.name) / "matlab"
        self.fake_matlab.write_text(FAKE_MATLAB.format(python=sys.executable))
        self.fake_matlab.chmod(0o755)
        tmpl_run = Run.objects.create(tool=Tool.objects.create(code="upload", version="1.0.0"), status=Run.Status.DONE)
        tmpl_rel = "support/flytracker/templates/t.mat"
        (self.root / tmpl_rel).parent.mkdir(parents=True)
        (self.root / tmpl_rel).write_bytes(calibration_file(ppm=10.0, r=300.0))
        self.template_asset = Asset.objects.create(
            type=asset_type(("flytracker_calibration", 1)), rel_path=tmpl_rel, sha256="0" * 64,
            size_bytes=(self.root / tmpl_rel).stat().st_size, meta={"ppm": 10.0, "arena_r_px": 300.0},
            produced_by=tmpl_run)
        # one crop per arena, as the crop step would have made them
        crop_run = Run.objects.create(tool=Tool.objects.create(code="crop", version="1.0.0"), status=Run.Status.DONE,
                                      recording=self.rec)
        for a in self.rec.arenas.all():
            rel = f"derived/{self.rec.code}/crop/1.0.0-run{crop_run.pk}/A{a.index:02d}.mp4"
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=64x48:rate=10",
                            "-pix_fmt", "yuv420p", "-y", str(self.root / rel)], check=True)
            Asset.objects.create(type=asset_type(CROP_VIDEO), rel_path=rel, sha256="0" * 64, size_bytes=1,
                                 meta={"fps": 10.0, "start_frame": 100, "n_frames": 20}, recording=self.rec, arena=a,
                                 produced_by=crop_run)
        self.worker.tools = ["flytracker@1.0.0"]
        self.worker.save()

    def worker_cfg(self, **extra):
        cmd = [sys.executable, str(REPO / "tools" / "flytracker" / "track.py"), "--matlab", str(self.fake_matlab),
               "--flytracker", self.tmp.name]
        return {"name": "rig1-win", "server": self.live_server_url, "token": self.token,
                "scratch": str(Path(self.tmp.name) / "scratch"), "tools": {"flytracker@1.0.0": {"command": cmd}},
                "storage": {"mode": "path", "root": str(self.root)}, **extra}

    def run_worker(self):
        w = load_worker()
        cfg = self.worker_cfg()
        return w.work_once(cfg, w.Api(cfg["server"], cfg["token"]), w.Storage(cfg["storage"]))

    def test_no_template_no_tracking(self):
        self.assertEqual(plan_tracking(), 0)

    def test_circle_mode_end_to_end(self):
        TrackerTemplate.objects.create(name="t", layout=self.rec.layout, mode="circle", asset=self.template_asset)
        excluded = self.rec.arenas.get(index=4)
        excluded.status = "excluded"
        excluded.save()
        g = self.rec.arenas.get(index=1).fly_groups.get()
        g.n_flies = 5
        g.save()
        self.assertEqual((plan_tracking(), plan_tracking()), (3, 0))             # arena 4 excluded; idempotent

        run = Run.objects.filter(tool__code="flytracker", arena__index=1).get()
        Run.objects.filter(tool__code="flytracker").exclude(pk=run.pk).update(status=Run.Status.CANCELLED)
        self.assertTrue(self.run_worker())
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.COMMITTING, run.error)
        p = run.params
        self.assertEqual((p["mode"], p["n_flies"], p["options"]["arena_r_mm"], p["options"]["force_arena_calib"]),
                         ("circle", 5, 30.0, True))
        self.assertEqual((p["fps"], p["start_frame"], p["expected_ppm"]), (10.0, 100, 10.0))

        commit_pending()
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.DONE, run.error)
        traj = Asset.objects.get(type__code="trajectories")
        self.assertEqual((traj.arena.index, traj.meta["n_flies"], traj.meta["n_frames"], traj.meta["n_flags"]), (1, 5, 20, 2))
        self.assertEqual(traj.meta["flags"][0], {"flies": [1, 2], "frames": [104, 107], "ambiguity": 0.5})
        self.assertEqual(traj.meta["frame_range"], [100, 119])                   # raw-video frames
        self.assertEqual(traj.warnings, [])
        import pandas as pd
        df = pd.read_parquet(self.root / traj.rel_path)
        self.assertAlmostEqual(float(df.loc[df.fly == 1, "x_mm"].iloc[0]), 10.0, places=4)   # 100 px / 10 px/mm
        self.assertAlmostEqual(float(df["t_s"].iloc[0]), 10.0)                   # frame 100 at 10 fps
        types = set(Asset.objects.filter(produced_by=run).values_list("type__code", flat=True))
        self.assertEqual(types, {"trajectories", "flytracker_track", "flytracker_feat", "flytracker_calibration"})
        self.assertEqual(set(run.inputs.values_list("type__code", flat=True)), {"crop_video", "flytracker_calibration"})

        self.client.force_login(User.objects.create_superuser("op", "", "x"))
        page = self.client.get("/inbox/tracking/").content.decode()
        self.assertIn("A01", page)
        self.assertIn("<b>2</b>", page)

    def test_wrong_scale_and_fly_count_are_flagged(self):
        TrackerTemplate.objects.create(name="t", layout=self.rec.layout, mode="circle", asset=self.template_asset)
        plan_tracking()
        run = Run.objects.filter(tool__code="flytracker", arena__index=1).get()
        Run.objects.filter(tool__code="flytracker").exclude(pk=run.pk).update(status=Run.Status.CANCELLED)
        self.template_asset.meta = {"ppm": 5.0}                                 # template expected 5 px/mm
        self.template_asset.save()
        self.run_worker()
        commit_pending()
        traj = Asset.objects.get(type__code="trajectories")
        self.assertTrue(any("Scale" in w for w in traj.warnings), traj.warnings)

    def test_fullframe_mode_keeps_template_scale(self):
        TrackerTemplate.objects.create(name="old", layout=self.rec.layout, mode="fullframe", asset=self.template_asset)
        plan_tracking()
        run = Run.objects.filter(tool__code="flytracker", arena__index=2).get()
        Run.objects.filter(tool__code="flytracker").exclude(pk=run.pk).update(status=Run.Status.CANCELLED)
        self.run_worker()
        commit_pending()
        run.refresh_from_db()
        self.assertNotIn("force_arena_calib", run.params["options"])
        traj = Asset.objects.get(type__code="trajectories")
        self.assertEqual(traj.meta["calibration"]["ppm"], 10.0)
        self.assertFalse(Asset.objects.filter(type__code="flytracker_calibration", produced_by=run).exists())
