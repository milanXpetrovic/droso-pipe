"""Pages: recordings, one recording, review, experiments, machines, settings. Run: python manage.py test web"""

import json
import shutil

import numpy as np
import pandas as pd
from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse

from core.formats import CROP_VIDEO, TRAJECTORIES, asset_type
from core.models import Asset, Review, Run, Tool, TrackerTemplate, Worker
from core.quality import compute
from core.tests import Store, calibration_file
from domain.models import Arena, Condition, Experiment, Genotype

from . import status as S


def fly_frame(n_flies=4, n_frames=600, fps=10.0, lost=0.0, jumps=0, seed=1):
    """Flies walking slowly (~5 mm/s) around; optional lost positions and teleports."""
    rng = np.random.default_rng(seed)
    rows = []
    for fly in range(1, n_flies + 1):
        steps = rng.normal(0, 0.35, size=(n_frames, 2))
        xy = 30 + np.cumsum(steps, axis=0).clip(-25, 25)
        for k in range(jumps):
            xy[100 + 37 * k:, 0] += 15 * (-1) ** k
        rows.append(pd.DataFrame({"frame": np.arange(n_frames), "fly": fly, "x_mm": xy[:, 0], "y_mm": xy[:, 1]}))
    df = pd.concat(rows, ignore_index=True)
    if lost:
        df.loc[df.sample(frac=lost, random_state=seed).index, ["x_mm", "y_mm"]] = np.nan
    df["t_s"] = df["frame"] / fps
    df["heading_rad"] = 0.0
    df["x_px"], df["y_px"] = df["x_mm"] * 10, df["y_mm"] * 10
    return df


class QualityTests(TestCase):
    def test_clean_tracking_is_good(self):
        q = compute(fly_frame(), fps=10, expected_flies=4, flags_per_min=1.0, ppm=10.0, expected_ppm=10.1)
        self.assertEqual((q["verdict"], q["flies_found"], q["jumps"]), ("good", 4, 0))
        self.assertEqual(q["reasons"], [])

    def test_problems_are_named(self):
        q = compute(fly_frame(jumps=4), fps=10, expected_flies=5, flags_per_min=19.0, ppm=11.0, expected_ppm=10.0)
        self.assertEqual(q["verdict"], "check")
        text = " ".join(q["reasons"])
        for words in ("4 flies tracked, 5 entered", "identity swaps (19 per minute)", "impossible jumps", "larger"):
            self.assertIn(words, text)

    def test_lost_flies_are_bad(self):
        q = compute(fly_frame(lost=0.3), fps=10, expected_flies=4)
        self.assertEqual(q["verdict"], "bad")
        self.assertIn("lost in 30%", q["reasons"][0])


class PagesBase(Store, TestCase):
    """The Store recording (2x2, raw video ingested) plus a logged-in operator."""

    def setUp(self):
        super().setUp()
        self.media = override_settings(MEDIA_ROOT=self.root / "media")         # frame previews go to the temp store
        self.media.enable()
        self.addCleanup(self.media.disable)
        self.user = User.objects.create_user("bobana", password="x")
        self.user.user_permissions.add(Permission.objects.get(codename="add_recording"))
        self.client.force_login(self.user)
        self.rec.operator = self.user
        self.rec.save()

    def crop(self):
        run = Run.objects.create(tool=Tool.objects.get_or_create(code="crop", version="1.0.0")[0],
                                 status=Run.Status.DONE, recording=self.rec)
        crops = {}
        for a in self.rec.arenas.all():
            rel = f"derived/{self.rec.code}/crop/1.0.0-run{run.pk}/A{a.index:02d}.mp4"
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / self.raw.rel_path, self.root / rel)        # a real video, good enough
            crops[a.index] = Asset.objects.create(
                type=asset_type(CROP_VIDEO), rel_path=rel, sha256="c" * 64, size_bytes=1, recording=self.rec, arena=a,
                produced_by=run, meta={"width": 320, "height": 240, "n_frames": 60, "fps": 10.0})
        return crops

    def track(self, crops, arenas=(1, 2, 3, 4), quality=True, **kw):
        tool = Tool.objects.get_or_create(code="flytracker", version="1.0.0")[0]
        out = {}
        for i in arenas:
            a = self.rec.arenas.get(index=i)
            run = Run.objects.create(tool=tool, status=Run.Status.DONE, recording=self.rec, arena=a,
                                     params={"fps": 10.0, "n_flies": 4, "expected_ppm": 10.0})
            run.inputs.add(crops[i])
            rel = f"derived/{self.rec.code}/flytracker/1.0.0-run{run.pk}/A{i:02d}-trajectories.parquet"
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            df = fly_frame(seed=i, **kw)
            df.to_parquet(self.root / rel, index=False)
            meta = {"n_flies": 4, "n_frames": 600, "flags_per_min": 2.0, "calibration": {"ppm": 10.0},
                    "flags": [{"flies": [1, 2], "frames": [f, f + 2], "ambiguity": 0.4} for f in (50, 60, 400)],
                    "frame_range": [0, 599]}
            asset = Asset.objects.create(type=asset_type(TRAJECTORIES), rel_path=rel, sha256="t" * 64, size_bytes=1,
                                         recording=self.rec, arena=a, produced_by=run, meta=meta)
            if quality:
                from core.quality import ensure_quality
                ensure_quality(asset)
            out[i] = asset
        return out

    def template(self, old=False):
        upload = Run.objects.create(tool=Tool.objects.get_or_create(code="upload", version="1.0.0")[0],
                                    status=Run.Status.DONE)
        rel = "support/flytracker/templates/t.mat"
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_bytes(calibration_file(old=old))
        asset = Asset.objects.create(type=asset_type(("flytracker_calibration", 1)), rel_path=rel, sha256="0" * 64,
                                     size_bytes=1, produced_by=upload, meta={"ppm": 10.0})
        return TrackerTemplate.objects.create(name="t", layout=self.rec.layout, asset=asset)

    def status(self):
        from web.views import recordings_queryset, tracking_ready
        return S.recording_status(recordings_queryset().get(pk=self.rec.pk), tracking_ready())


class StatusTests(PagesBase):
    def test_raw_only_waits_for_crop(self):
        st = self.status()
        self.assertEqual([s.state for s in st.steps], ["done", "done", "waiting", "not_started", "not_started"])
        self.assertEqual(st.group, S.PROGRESS)

    def test_crops_without_tracking_setup_need_attention(self):
        self.crop()
        st = self.status()
        self.assertEqual(st.group, S.ATTENTION)
        self.assertIn("not set up", st.sentence)
        self.template()
        self.assertEqual(self.status().group, S.PROGRESS)

    def test_tracked_then_reviewed(self):
        self.template()
        traj = self.track(self.crop())
        st = self.status()
        self.assertEqual((st.group, len(st.to_review), st.steps[4].state), (S.REVIEW, 4, "needs_you"))
        self.assertEqual(st.quality_label, "4 good")
        for a in traj.values():
            Review.objects.create(asset=a, decision=Review.Decision.ACCEPTED)
        st = self.status()
        self.assertEqual((st.group, st.quality_label), (S.FINISHED, "4 accepted"))
        self.assertEqual(st.steps[4].text, "4 accepted")

    def test_failed_tracking_is_attention(self):
        self.template()
        crops = self.crop()
        run = Run.objects.create(tool=Tool.objects.get_or_create(code="flytracker", version="1.0.0")[0],
                                 status=Run.Status.FAILED, recording=self.rec, arena=self.rec.arenas.get(index=1),
                                 error="RuntimeError: tool exited with 1")
        run.inputs.add(crops[1])
        st = self.status()
        self.assertEqual(st.group, S.ATTENTION)
        self.assertEqual(st.arenas[0].state, "failed")


class RecordingsPageTests(PagesBase):
    def test_list_groups_and_filters(self):
        self.template()
        self.track(self.crop())
        r = self.client.get(reverse("web:recordings"))
        self.assertContains(r, self.rec.code)
        self.assertContains(r, "Ready for review · 1")
        self.assertContains(r, reverse("web:review_item", args=[Asset.objects.filter(type__code="trajectories")
                                                                .order_by("arena__index").first().pk]))
        self.assertNotContains(self.client.get(reverse("web:recordings") + "?show=done"), self.rec.code)
        self.assertNotContains(self.client.get(reverse("web:recordings") + "?q=nothing-like-this"), self.rec.code)

    def test_only_mine_by_default(self):
        self.rec.operator = None
        self.rec.save()
        self.assertNotContains(self.client.get(reverse("web:recordings")), self.rec.code)
        self.assertContains(self.client.get(reverse("web:recordings") + "?mine=0"), self.rec.code)

    def test_recording_page(self):
        self.template()
        self.track(self.crop(), arenas=(1, 2))
        r = self.client.get(reverse("web:recording", args=[self.rec.code]))
        self.assertContains(r, "Review 2 arenas")
        self.assertContains(r, "looks good")
        self.assertContains(r, "A03")


class ReviewTests(PagesBase):
    def test_review_flow(self):
        self.template()
        traj = self.track(self.crop(), arenas=(1, 2))
        r = self.client.get(reverse("web:review_next"))
        self.assertRedirects(r, reverse("web:review_item", args=[traj[1].pk]))
        page = self.client.get(reverse("web:review_item", args=[traj[1].pk]))
        self.assertContains(page, "Suggestion: looks good.")
        self.assertContains(page, "Possible identity swaps")
        self.assertEqual(page.context["waiting"], 2)

        r = self.client.post(reverse("web:review_item", args=[traj[1].pk]), {"decision": "accept", "note": "fine"},
                             follow=True)
        self.assertEqual(r.redirect_chain[-1][0], reverse("web:review_item", args=[traj[2].pk]))   # straight to the next
        review = Review.objects.get(asset=traj[1])
        self.assertEqual((review.decision, review.decided_by, review.note), ("accepted", self.user, "fine"))

        self.client.post(reverse("web:review_item", args=[traj[2].pk]), {"decision": "unusable", "reason": "A fly died"})
        self.assertEqual(Review.objects.get(asset=traj[2]).reason, "A fly died")
        self.assertContains(self.client.get(reverse("web:review_next")), "Nothing to review")

        # changing one's mind replaces the decision, and keeps its history
        self.client.post(reverse("web:review_item", args=[traj[1].pk]), {"decision": "tracking_bad"})
        self.assertEqual(Review.objects.get(asset=traj[1]).decision, "tracking_bad")
        self.assertEqual(Review.history.filter(asset_id=traj[1].pk).count(), 2)

    def test_paths_and_frame(self):
        self.template()
        traj = self.track(self.crop(), arenas=(1,))
        svg = self.client.get(reverse("web:paths", args=[traj[1].pk]))
        self.assertEqual(svg["Content-Type"], "image/svg+xml")
        self.assertEqual(svg.content.decode().count("<polyline"), 4)
        self.assertIn('viewBox="0 0 320 240"', svg.content.decode())
        frame = self.client.get(reverse("web:arena_frame", args=[traj[1].pk]))
        self.assertEqual(frame["Content-Type"], "image/jpeg")
        frame.close()

    def test_quality_filled_in_for_older_results(self):
        self.template()
        traj = self.track(self.crop(), arenas=(1,), quality=False)
        self.assertNotIn("quality", traj[1].meta)
        self.client.get(reverse("web:review_item", args=[traj[1].pk]))
        traj[1].refresh_from_db()
        self.assertEqual(traj[1].meta["quality"]["verdict"], "good")


class ExperimentsTests(PagesBase):
    def test_usable_counts(self):
        self.template()
        traj = self.track(self.crop(), arenas=(1, 2))
        Review.objects.create(asset=traj[1], decision=Review.Decision.ACCEPTED)
        Review.objects.create(asset=traj[2], decision=Review.Decision.TRACKING_BAD)
        r = self.client.get(reverse("web:experiments"))
        row = r.context["experiments"][0]["conds"][0]
        self.assertEqual((row["recorded"], row["usable"], row["accepted"], row["kept"]), (1, 1, 1, 1))
        self.assertContains(r, "1 / 20")

    def test_quick_create(self):
        r = self.client.post(reverse("web:experiment_new"), {"code": "Iso-2026", "name": "Isolation"},
                             HTTP_ACCEPT="application/json")
        self.assertEqual(r.json()["label"], "Iso-2026")
        self.assertEqual(Experiment.objects.get(code="Iso-2026").owner, self.user)
        r = self.client.post(reverse("web:condition_new"), {"code": "w1118-Iso3d", "genotype_new": "w1118"},
                             HTTP_ACCEPT="application/json")
        self.assertEqual(Condition.objects.get(pk=r.json()["id"]).genotype, Genotype.objects.get(code="w1118"))
        bad = self.client.post(reverse("web:condition_new"), {"code": "has space", "genotype_new": "w1118"})
        self.assertEqual(bad.status_code, 400)


class MachinesTests(PagesBase):
    def test_paused_worker_and_retry(self):
        r = self.client.post("/api/worker/status", json.dumps({"state": "paused", "detail": "recording software running"}),
                             content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(r.status_code, 204)
        page = self.client.get(reverse("web:machines"))
        self.assertContains(page, "Paused")
        self.assertContains(page, "recording software running")

        crop = Run.objects.create(tool=Tool.objects.get_or_create(code="crop", version="1.0.0")[0], recording=self.rec,
                                  status=Run.Status.FAILED, attempts=3, error="PermissionError: access denied")
        crop.inputs.add(self.raw)
        page = self.client.get(reverse("web:machines"))
        self.assertContains(page, "access denied")
        self.assertNotContains(page, "Try again")                        # operators cannot retry
        self.assertEqual(self.client.post(reverse("web:run_retry", args=[crop.pk])).status_code, 403)

        admin = User.objects.create_superuser("milan", "", "x")
        self.client.force_login(admin)
        self.client.post(reverse("web:run_retry", args=[crop.pk]))
        crop.refresh_from_db()
        self.assertEqual((crop.status, crop.attempts, crop.error), ("pending", 0, ""))

        self.client.post("/api/runs/claim", json.dumps({"tools": ["crop@1.0.0"]}), content_type="application/json",
                         HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(Worker.objects.get().state, "")                # asking for work again: no longer paused


class SettingsTests(PagesBase):
    def test_old_template_and_camera_speed(self):
        self.template(old=True)
        self.raw.meta = {**self.raw.meta, "fps": 24.0, "fps_used": 29.45}
        Asset.objects.filter(pk=self.raw.pk).update(meta=self.raw.meta)
        r = self.client.get(reverse("web:settings"))
        self.assertContains(r, "Needs a new calibration")
        self.assertContains(r, "old FlyTracker")
        self.assertContains(r, "Records at 29.45 fps")


class UploadArenasTests(PagesBase):
    def test_leave_out_an_arena_until_cropped(self):
        from intake.models import IncomingVideo
        v = IncomingVideo.objects.create(rel_path=self.raw.rel_path, rig=self.rec.rig, file_name="x.mp4", size_bytes=1,
                                         mtime=self.rec.started_at, changed_at=self.rec.started_at, recording=self.rec,
                                         status=IncomingVideo.Status.INGESTED, duration_s=6.0, fps=10.0)
        url = reverse("intake:upload_arenas", args=[v.pk])
        self.assertContains(self.client.get(url), "4 of 4 will be analysed")
        self.client.post(url, {"index": 2, "reason": "A fly escaped"})
        a = self.rec.arenas.get(index=2)
        self.assertEqual((a.status, a.exclusion_reason), (Arena.Status.EXCLUDED, "A fly escaped"))
        self.client.post(url, {"index": 2})
        self.assertEqual(self.rec.arenas.get(index=2).status, Arena.Status.OK)
        self.crop()
        self.client.post(url, {"index": 3})
        self.assertEqual(self.rec.arenas.get(index=3).status, Arena.Status.OK)      # locked once cut out
        self.assertContains(self.client.get(url), "already cut out")


class DeleteRecordingTests(PagesBase):
    def test_delete_test_recording(self):
        from intake.models import IncomingVideo
        self.template()
        traj = self.track(self.crop(), arenas=(1, 2))
        Review.objects.create(asset=traj[1], decision=Review.Decision.ACCEPTED)
        IncomingVideo.objects.create(rel_path="incoming/rig1/x.mp4", rig=self.rec.rig, file_name="x.mp4", size_bytes=1,
                                     mtime=self.rec.started_at, changed_at=self.rec.started_at, recording=self.rec,
                                     status=IncomingVideo.Status.INGESTED)
        url, code = reverse("web:recording_delete", args=[self.rec.code]), self.rec.code
        raw_dir, derived = (self.root / self.raw.rel_path).parent, self.root / "derived" / code
        self.assertEqual(self.client.post(url, {"confirm": code}).status_code, 403)        # operators cannot
        self.assertNotContains(self.client.get(reverse("web:recording", args=[code])), "Delete this recording")

        self.client.force_login(User.objects.create_superuser("milan", "", "x"))
        self.assertContains(self.client.get(reverse("web:recording", args=[code])), "Delete this recording")
        self.client.post(url, {"confirm": "wrong"})
        self.assertTrue(type(self.rec).objects.filter(code=code).exists())                 # wrong code: nothing

        busy = Run.objects.filter(tool__code="flytracker").first()
        Run.objects.filter(pk=busy.pk).update(status=Run.Status.RUNNING, worker="rig1-win")
        r = self.client.post(url, {"confirm": code}, follow=True)
        self.assertContains(r, "rig1-win is working on this recording")
        Run.objects.filter(pk=busy.pk).update(status=Run.Status.DONE)

        r = self.client.post(url, {"confirm": code}, follow=True)
        self.assertContains(r, f"{code} deleted")
        self.assertFalse(type(self.rec).all_objects.filter(code=code).exists())
        self.assertFalse(Asset.objects.filter(recording__isnull=False).exists())
        self.assertFalse(Run.objects.exclude(tool__code="upload").exists())
        self.assertFalse(Review.objects.exists() or IncomingVideo.objects.exists() or Arena.all_objects.exists())
        self.assertFalse(raw_dir.exists() or derived.exists())
        self.assertTrue(TrackerTemplate.objects.exists())                                  # set-up stays
