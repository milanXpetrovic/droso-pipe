"""Intake: file-name parsing, scanning incoming/, registering a video. Run: python manage.py test intake"""

import datetime as dt
import os
import tempfile
from pathlib import Path

from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from domain.models import Arena, ArenaLayout, Camera, Condition, Experiment, FlyGroup, Genotype, Recording, Rig

from .models import IncomingVideo
from .naming import parse_basler_name
from .scanner import scan

NAME = "Basler acA4024-29um (23517383)_20260203_111507240.mp4"


class NamingTests(TestCase):
    def test_basler_name(self):
        p = parse_basler_name(NAME)
        self.assertEqual((p["camera_model"], p["camera_serial"]), ("acA4024-29um", "23517383"))
        self.assertEqual(p["started_at"].astimezone(dt.timezone.utc),
                         dt.datetime(2026, 2, 3, 10, 15, 7, 240000, tzinfo=dt.timezone.utc))   # CET = UTC+1

    def test_summer_time(self):
        p = parse_basler_name("Basler acA4024-29um (23517383)_20260714_093000000.mp4")
        self.assertEqual(p["started_at"].astimezone(dt.timezone.utc).hour, 7)                  # CEST = UTC+2

    def test_other_names(self):
        self.assertIsNone(parse_basler_name("my_video.avi"))
        self.assertIsNone(parse_basler_name("Basler x (1)_20261399_000000000.mp4"))           # invalid date


class Base(TestCase):
    fixtures = ["catalogs", "lab"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "incoming" / "rig1").mkdir(parents=True)
        (self.root / "incoming" / "rig2").mkdir(parents=True)
        self.settings_override = override_settings(VIDEOS_ROOT=self.root, INCOMING_STABLE_SECONDS=60)
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        self.tmp.cleanup()

    def drop(self, name=NAME, rig="rig1", size=1000):
        f = self.root / "incoming" / rig / name
        f.write_bytes(b"\0" * size)
        return f


class ScannerTests(Base):
    def test_new_file_waits_until_copy_is_finished(self):
        self.drop()
        t0 = timezone.now()
        self.assertEqual(scan(now=t0)["new"], 1)
        v = IncomingVideo.objects.get()
        self.assertEqual((v.rig.code, v.camera_serial), ("R1", "23517383"))
        self.assertFalse(v.is_stable(t0 + dt.timedelta(seconds=30)))
        self.assertTrue(v.is_stable(t0 + dt.timedelta(seconds=61)))

    def test_growing_file_resets_the_clock(self):
        f = self.drop(size=100)
        t0 = timezone.now()
        scan(now=t0)
        f.write_bytes(b"\0" * 200)
        os.utime(f, (t0.timestamp() + 5, t0.timestamp() + 5))
        scan(now=t0 + dt.timedelta(seconds=50))
        self.assertFalse(IncomingVideo.objects.get().is_stable(t0 + dt.timedelta(seconds=70)))

    def test_ignores_other_files_and_folders(self):
        self.drop(name="notes.txt")
        self.drop(name=".hidden.mp4")
        (self.root / "incoming" / "unknown").mkdir()
        self.drop(rig="unknown")
        scan()
        self.assertEqual(IncomingVideo.objects.count(), 0)

    def test_disappeared_file(self):
        f = self.drop()
        scan()
        f.unlink()
        scan()
        self.assertEqual(IncomingVideo.objects.get().status, IncomingVideo.Status.MISSING)


class RegisterBase(Base):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("op", password="x")
        self.user.user_permissions.add(Permission.objects.get(codename="add_recording"))
        self.client.force_login(self.user)
        Camera.objects.filter(code="1").update(serial="23517383")
        ArenaLayout.objects.filter(code="2x2").update(arena_diameter_mm=60)
        self.exp = Experiment.objects.create(code="ISO-2026", name="Isolation")
        self.ctrl = Condition.objects.get(code="Control")
        self.iso = Condition.objects.create(code="Iso7d", genotype=Genotype.objects.get(code="CS"))

    def ready_video(self, **kw):
        self.drop(**kw)
        scan(now=timezone.now() - dt.timedelta(minutes=5))
        return IncomingVideo.objects.latest("first_seen_at")

    def post_form(self, video, **extra):
        data = {"experiment": self.exp.pk, "layout": ArenaLayout.objects.get(code="2x2").pk,
                "condition": self.ctrl.pk, "sex": "M", "n_flies": 12, **extra}
        return self.client.post(reverse("intake:register", args=[video.pk]), data)



class RegisterTests(RegisterBase):
    def test_inbox_lists_video(self):
        self.ready_video()
        r = self.client.get(reverse("intake:inbox"))
        self.assertContains(r, "23517383")
        self.assertContains(r, "Enter metadata")

    def test_register_creates_linked_recording(self):
        v = self.ready_video()
        r = self.post_form(v)
        rec = Recording.objects.get()
        self.assertRedirects(r, reverse("intake:arenas", args=[rec.code]))
        self.assertEqual(rec.code, "R1-20260203-001")
        self.assertEqual((rec.rig.code, rec.camera.code, rec.operator), ("R1", "1", self.user))
        self.assertEqual(rec.started_at, parse_basler_name(NAME)["started_at"])
        self.assertEqual(rec.arenas.count(), 4)
        v.refresh_from_db()
        self.assertEqual((v.status, v.recording), (IncomingVideo.Status.REGISTERED, rec))
        self.assertEqual(self.post_form(v).status_code, 404)          # cannot register twice

    def test_started_at_from_file_cannot_be_overridden(self):
        v = self.ready_video()
        self.post_form(v, started_at="2020-01-01T00:00:00")
        self.assertEqual(Recording.objects.get().started_at.year, 2026)

    def test_form_prefilled_from_last_recording_on_rig(self):
        self.post_form(self.ready_video(), n_flies=24, incubator="3")
        v2 = self.ready_video(name="Basler acA4024-29um (23517383)_20260203_120000000.mp4")
        form = self.client.get(reverse("intake:register", args=[v2.pk])).context["form"]
        self.assertEqual((form.initial["n_flies"], form.initial["incubator"]), (24, "3"))

    def test_unknown_camera(self):
        v = self.ready_video(name="Basler acA4024-29um (99999999)_20260203_111507240.mp4")
        r = self.client.get(reverse("intake:register", args=[v.pk]))
        self.assertContains(r, "Unknown camera")
        self.assertContains(r, "serial=99999999")

    def test_camera_on_unusual_rig_warns(self):
        v = self.ready_video(rig="rig2")                  # camera 1 usually sits on R1
        r = self.client.get(reverse("intake:register", args=[v.pk]))
        self.assertContains(r, "Were the cameras swapped?")

    def test_name_without_metadata_needs_manual_time_and_camera(self):
        v = self.ready_video(name="something.avi")
        self.assertEqual(self.post_form(v).status_code, 200)              # missing camera and time
        self.post_form(v, camera=Camera.objects.get(code="2").pk, started_at="2026-02-03T11:15:07")
        self.assertEqual(Recording.objects.get().camera.code, "2")

    def test_still_copying_is_refused(self):
        self.drop()
        scan()
        v = IncomingVideo.objects.get()
        self.assertRedirects(self.client.get(reverse("intake:register", args=[v.pk])), reverse("intake:inbox"))

    def test_arenas_page(self):
        self.post_form(self.ready_video())
        rec = Recording.objects.get()
        arenas = list(rec.arenas.order_by("index"))
        groups = list(FlyGroup.objects.filter(arena__recording=rec).order_by("arena__index"))
        data = {"arena-TOTAL_FORMS": 4, "arena-INITIAL_FORMS": 4, "group-TOTAL_FORMS": 4, "group-INITIAL_FORMS": 4}
        for i, a in enumerate(arenas):
            data |= {f"arena-{i}-id": a.pk, f"arena-{i}-status": "ok"}
        for i, g in enumerate(groups):
            data |= {f"group-{i}-id": g.pk, f"group-{i}-condition": self.ctrl.pk, f"group-{i}-sex": "M",
                     f"group-{i}-n_flies": 12}
        data |= {"arena-2-status": "excluded", "arena-3-status": "empty", "group-1-condition": self.iso.pk}
        r = self.client.post(reverse("intake:arenas", args=[rec.code]), data)
        self.assertRedirects(r, reverse("web:recording", args=[rec.code]))
        self.assertEqual([a.status for a in rec.arenas.order_by("index")], ["ok", "ok", "excluded", "empty"])
        self.assertEqual(FlyGroup.objects.filter(arena__recording=rec).count(), 3)       # empty arena has no flies
        self.assertEqual(FlyGroup.objects.get(arena__recording=rec, arena__index=2).condition, self.iso)

    def test_ignore(self):
        v = self.ready_video()
        self.client.post(reverse("intake:ignore", args=[v.pk]), {"reason": "test file"})
        v.refresh_from_db()
        self.assertEqual(v.status, IncomingVideo.Status.IGNORED)

    def test_requires_permission(self):
        self.client.force_login(User.objects.create_user("guest", password="x"))
        self.assertEqual(self.client.get(reverse("intake:inbox")).status_code, 403)


class PreviewBase(RegisterBase):
    """Uses a real 6-second test video made by ffmpeg."""

    def make_video(self, rig="rig1", name=NAME, recorded_s=6.0):
        """recorded_s: wall-clock recording time, i.e. file written that long after the start in its name."""
        import subprocess
        from .preview import FFMPEG
        out = self.root / "incoming" / rig / name
        subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=10",
                        "-pix_fmt", "yuv420p", "-y", str(out)], check=True)
        parsed = parse_basler_name(name)
        if parsed and recorded_s is not None:
            end = parsed["started_at"].timestamp() + recorded_s
            os.utime(out, (end, end))
        scan(now=timezone.now() - dt.timedelta(minutes=5))
        return IncomingVideo.objects.get(file_name=name)

    def setUp(self):
        super().setUp()
        self.media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()

    def tearDown(self):
        self.media_override.disable()
        self.media.cleanup()
        super().tearDown()



class PreviewTests(PreviewBase):
    def test_duration_is_read_and_cached(self):
        v = self.make_video()
        self.assertAlmostEqual(v.ensure_duration(), 6.0, delta=0.2)
        v.refresh_from_db()
        self.assertIsNotNone(v.duration_s)

    def test_frame_is_a_jpeg(self):
        v = self.make_video()
        r = self.client.get(reverse("intake:frame", args=[v.pk]) + "?t=2.5")
        self.assertEqual(r["Content-Type"], "image/jpeg")
        self.assertEqual(b"".join(r.streaming_content)[:2], b"\xff\xd8")
        r = self.client.get(reverse("intake:frame", args=[v.pk]))          # random time
        self.assertEqual(r.status_code, 200)

    def test_arenas_page_shows_frame_and_labels(self):
        v = self.make_video()
        self.post_form(v)
        html = self.client.get(reverse("intake:arenas", args=[Recording.objects.get().code])).content.decode()
        self.assertIn(reverse("intake:frame", args=[v.pk]), html)
        for label in ("A01", "A02", "A03", "A04"):
            self.assertIn(f'id="ov-{int(label[1:])}"', html)

    def test_register_page_shows_frame(self):
        v = self.make_video()
        self.assertContains(self.client.get(reverse("intake:register", args=[v.pk])), "Another frame")

    def test_unreadable_video_has_no_preview(self):
        v = self.ready_video()                    # zeros, not a real video
        self.assertNotContains(self.client.get(reverse("intake:register", args=[v.pk])), "Another frame")


class ProbeTests(TestCase):
    """Header reading for different containers and frame rates."""

    def make(self, name, rate, codec_args):
        import subprocess
        from .preview import FFMPEG
        out = Path(tempfile.mkdtemp()) / name
        subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration=3:size=320x240:rate={rate}",
                        *codec_args, "-y", str(out)], check=True)
        return out

    def test_mp4_24fps(self):
        from .preview import probe
        info = probe(self.make("a.mp4", 24, ["-pix_fmt", "yuv420p"]))
        self.assertEqual((info["fps"], info["width"], info["height"], info["codec"]), (24.0, 320, 240, "h264"))
        self.assertAlmostEqual(info["duration_s"], 3.0, delta=0.1)

    def test_avi_29_97fps(self):
        from .preview import probe
        info = probe(self.make("b.avi", "30000/1001", ["-c:v", "mjpeg"]))
        self.assertAlmostEqual(info["fps"], 29.97, places=2)
        self.assertEqual(info["codec"], "mjpeg")

    def test_not_a_video(self):
        from .preview import probe
        f = Path(tempfile.mkdtemp()) / "x.mp4"
        f.write_bytes(b"\0" * 100)
        self.assertIsNone(probe(f)["fps"])


class LengthTests(PreviewBase):
    """Video length shown in the UI and used to check the analysis window (6-second test video)."""

    def test_hms_filter(self):
        from .templatetags.intake_extras import hms
        self.assertEqual([hms(1872.4), hms(3725), hms(59.6), hms(None)], ["31:12", "1:02:05", "1:00", "—"])

    def test_length_in_inbox_and_form(self):
        v = self.make_video()
        inbox = self.client.get(reverse("intake:inbox"))
        self.assertContains(inbox, "0:06")
        v.refresh_from_db()
        self.assertEqual((v.fps, v.width, v.height, v.n_frames_expected), (10.0, 320, 240, 60))
        self.assertContains(self.client.get(reverse("intake:register", args=[v.pk])), "0:06")

    def test_window_must_fit_in_video(self):
        v = self.make_video()
        r = self.post_form(v, window_start_s=1, window_end_s=100)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "only 6.0 s long")
        self.post_form(v, window_start_s=1, window_end_s=5)
        self.assertEqual(Recording.objects.get().window_end_s, 5)

    def test_protocol_length_feeds_the_helper(self):
        from domain.models import Protocol
        p = Protocol.objects.create(code="free-5s", duration_s=5)
        v = self.make_video()
        html = self.client.get(reverse("intake:register", args=[v.pk])).content.decode()
        self.assertIn('id="protocol-durations"', html)
        self.assertIn(f'"{p.pk}": 5', html)
        self.assertIn('data-win="last"', html)


class IngestTests(PreviewBase):
    """End to end: registered video in incoming/ -> raw_video asset in raw/."""

    def registered(self, **kw):
        v = self.make_video(**kw)
        self.post_form(v)
        v.refresh_from_db()
        return v

    def test_ingest_moves_verifies_and_registers(self):
        import json
        import stat
        from core.models import Asset, Run
        from .ingest import ingest_pending, sha256_of
        v = self.registered()
        src = self.root / v.rel_path
        src_sha = sha256_of(src)
        self.assertEqual(ingest_pending(), 1)

        v.refresh_from_db()
        self.assertEqual(v.status, IncomingVideo.Status.INGESTED)
        self.assertFalse(src.exists())                                        # removed from incoming/
        asset = Asset.objects.get()
        self.assertEqual(asset.rel_path, "raw/2026/02/R1-20260203-001/video.mp4")
        dest = self.root / asset.rel_path
        self.assertEqual((asset.sha256, sha256_of(dest)), (src_sha, src_sha))    # identical copy
        self.assertFalse(dest.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))   # read-only
        self.assertEqual((asset.meta["n_frames"], asset.meta["fps"]), (60, 10.0))
        self.assertEqual(asset.warnings, [])
        self.assertEqual((asset.produced_by.tool.code, asset.produced_by.status), ("ingest", Run.Status.DONE))

        sidecar = json.loads((dest.parent / "recording.json").read_text())
        self.assertEqual((sidecar["code"], len(sidecar["arenas"])), ("R1-20260203-001", 4))
        manifest = json.loads((dest.parent / "manifest.json").read_text())
        self.assertEqual(manifest["asset"]["sha256"], src_sha)
        self.assertFalse(list(dest.parent.glob(".*.part")))                  # no leftovers

        # the frame preview now reads from raw/
        r = self.client.get(reverse("intake:frame", args=[v.pk]) + "?t=1")
        self.assertEqual(r["Content-Type"], "image/jpeg")
        self.assertContains(self.client.get(reverse("intake:inbox")), "60 frames")

    def test_camera_faster_than_the_file_says(self):
        """60 frames declared at 10 fps, but recorded in 4.8 s of real time: the camera ran at 12.5 fps."""
        from unittest import mock
        from core.models import Asset
        from .ingest import ingest_pending
        with mock.patch("core.formats.FINALIZE_SLACK_S", 0):            # the test video is only seconds long
            v = self.make_video(recorded_s=4.8)
            inbox = self.client.get(reverse("intake:inbox"))
            self.assertContains(inbox, "camera recorded at 12.50 fps")
            self.assertContains(inbox, "0:05")                           # 4.8 real seconds, not the file's 6
            self.assertContains(self.client.get(reverse("intake:register", args=[v.pk])), "12.50 fps")
            self.assertEqual(self.client.get(reverse("intake:frame", args=[v.pk]) + "?t=4")["Content-Type"],
                             "image/jpeg")
            self.post_form(v)
            ingest_pending()
        a = Asset.objects.get()
        self.assertEqual((a.meta["fps"], a.meta["fps_used"], a.meta["recorded_s"]), (10.0, 12.5, 4.8))
        self.assertIn("12.50 fps", a.warnings[0])

    def test_frame_rate_unchecked_when_file_time_is_unreliable(self):
        from core.models import Asset
        from .ingest import ingest_pending
        v = self.make_video(recorded_s=None)                              # file time = now, months after the name
        self.post_form(v)
        ingest_pending()
        a = Asset.objects.get()
        self.assertEqual(a.meta["fps_used"], 10.0)
        self.assertIn("Could not check the frame rate", a.warnings[0])

    def test_frame_mismatch_becomes_a_warning(self):
        from unittest import mock
        from core.models import Asset
        from .ingest import ingest_pending
        self.registered()
        with mock.patch("intake.ingest.count_frames", return_value=55):
            ingest_pending()
        self.assertIn("dropped", Asset.objects.get().warnings[0])

    def test_broken_video_is_kept_and_reported(self):
        from core.models import Asset, Run
        from .ingest import MAX_ATTEMPTS, ingest_pending
        v = self.ready_video()                                               # zeros, not a video
        self.post_form(v, camera=Camera.objects.get(code="1").pk)
        v.refresh_from_db()
        for _ in range(MAX_ATTEMPTS + 1):
            ingest_pending()
        v.refresh_from_db()
        self.assertEqual((v.status, v.ingest_attempts), (IncomingVideo.Status.REGISTERED, MAX_ATTEMPTS))
        self.assertIn("Not a valid video", v.ingest_error)
        self.assertTrue((self.root / v.rel_path).exists())                    # source untouched
        self.assertFalse(Asset.objects.exists())
        self.assertFalse(list((self.root / "raw").rglob("*.mp4")))           # nothing left in raw/
        self.assertEqual(Run.objects.filter(status=Run.Status.FAILED).count(), MAX_ATTEMPTS)
        self.assertContains(self.client.get(reverse("intake:inbox")), "Try again")
        self.client.post(reverse("intake:retry", args=[v.pk]))
        v.refresh_from_db()
        self.assertEqual(v.ingest_attempts, 0)

    def test_existing_raw_file_is_never_overwritten(self):
        from .ingest import ingest_pending
        v = self.registered()
        existing = self.root / "raw/2026/02/R1-20260203-001/video.mp4"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"precious")
        ingest_pending()
        v.refresh_from_db()
        self.assertIn("never overwritten", v.ingest_error)
        self.assertEqual(existing.read_bytes(), b"precious")

    def test_interrupted_ingest_is_requeued(self):
        from .ingest import recover_interrupted
        v = self.registered()
        IncomingVideo.objects.filter(pk=v.pk).update(status=IncomingVideo.Status.INGESTING)
        recover_interrupted()
        v.refresh_from_db()
        self.assertEqual(v.status, IncomingVideo.Status.REGISTERED)

    def test_command_runs_once(self):
        from django.core.management import call_command
        from io import StringIO
        self.registered()
        out = StringIO()
        call_command("ingest", stdout=out)
        self.assertIn("ingested: 1", out.getvalue())


class UploadTests(PreviewBase):
    """Browser upload: chunks, resume, metadata saved during the upload, then the usual ingest."""

    def video_bytes(self) -> bytes:
        import subprocess
        from .preview import FFMPEG
        src = self.root / "src.mp4"
        subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=10",
                        "-pix_fmt", "yuv420p", "-y", str(src)], check=True)
        return src.read_bytes()

    def start(self, data: bytes, name=NAME, recorded_s=6.0, rig=""):
        end_ms = int((parse_basler_name(name)["started_at"].timestamp() + recorded_s) * 1000) if parse_basler_name(name) else 0
        return self.client.post(reverse("intake:upload_start"),
                                {"name": name, "size": len(data), "last_modified": end_ms, "rig": rig})

    def put(self, video_id, data: bytes, offset: int):
        return self.client.put(reverse("intake:upload_chunk", args=[video_id]), data=data,
                               content_type="application/octet-stream", HTTP_X_OFFSET=str(offset))

    def send_all(self, video_id, data: bytes, offset=0, size=7000):
        r = None
        while offset < len(data):
            r = self.put(video_id, data[offset:offset + size], offset)
            self.assertEqual(r.status_code, 200, r.content)
            offset = r.json()["offset"]
        return r.json()

    def form_data(self, **extra):
        return {"experiment": self.exp.pk, "layout": ArenaLayout.objects.get(code="2x2").pk,
                "condition": self.ctrl.pk, "sex": "M", "n_flies": 12, **extra}

    def test_page(self):
        r = self.client.get(reverse("intake:upload"))
        self.assertContains(r, "Drag the video here")
        self.assertContains(r, "R2")                                     # rig choice

    def test_upload_with_metadata_saved_meanwhile_then_ingest(self):
        from unittest import mock
        from core.models import Asset
        from .ingest import ingest_pending
        data = self.video_bytes()
        with mock.patch("core.formats.FINALIZE_SLACK_S", 0):
            up = self.start(data, recorded_s=4.8).json()              # camera ran at 12.5 fps, file says 10
            self.assertEqual((up["offset"], up["rig"]), (0, "R1"))      # rig from the camera's serial
            video = IncomingVideo.objects.get(pk=up["id"])
            self.assertEqual(video.status, IncomingVideo.Status.UPLOADING)
            self.assertFalse((self.root / video.rel_path).exists())      # only the hidden .part so far
            scan()
            self.assertEqual(IncomingVideo.objects.count(), 1)           # the scanner ignores it

            form = self.client.get(up["form_url"])
            self.assertContains(form, 'id="register-form"')
            saved = self.client.post(up["form_url"], self.form_data())
            self.assertContains(saved, "Metadata saved")
            video.refresh_from_db()
            self.assertEqual(video.status, IncomingVideo.Status.UPLOADING)   # not ingestable before the last byte
            self.assertEqual(ingest_pending(), 0)

            last = self.send_all(up["id"], data)
        self.assertTrue(last["done"] and last["registered"])
        self.assertIn("12.50 fps", last["warning"])
        video.refresh_from_db()
        final = self.root / video.rel_path
        self.assertEqual(final.read_bytes(), data)
        self.assertAlmostEqual(final.stat().st_mtime, video.mtime.timestamp(), places=0)   # time of the recording PC
        self.assertFalse(list(final.parent.glob(".*.part")))
        self.assertEqual(video.status, IncomingVideo.Status.REGISTERED)

        with mock.patch("core.formats.FINALIZE_SLACK_S", 0):
            self.assertEqual(ingest_pending(), 1)
        self.assertEqual(Asset.objects.get().meta["fps_used"], 12.5)

    def test_resume_after_interruption(self):
        data = self.video_bytes()
        half = len(data) // 2
        up = self.start(data).json()
        self.assertEqual(self.put(up["id"], data[:half], 0).json(), {"offset": half, "done": False})
        again = self.start(data).json()                                 # same file dropped again
        self.assertEqual((again["id"], again["offset"]), (up["id"], half))
        wrong = self.put(up["id"], data[100:200], 100)                   # an old chunk sent twice
        self.assertEqual((wrong.status_code, wrong.json()["offset"]), (409, half))
        self.assertTrue(self.send_all(up["id"], data, offset=half, size=5000)["done"])
        self.assertEqual((self.root / IncomingVideo.objects.get().rel_path).read_bytes(), data)

    def test_metadata_after_the_upload(self):
        data = self.video_bytes()
        up = self.start(data).json()
        self.assertFalse(self.send_all(up["id"], data)["registered"])
        video = IncomingVideo.objects.get()
        self.assertEqual(video.status, IncomingVideo.Status.NEW)
        self.assertTrue(video.is_stable())                              # complete: no waiting for "copying…"
        self.assertContains(self.client.get(up["form_url"]), "Video length 0:06")
        self.client.post(up["form_url"], self.form_data())
        video.refresh_from_db()
        self.assertEqual(video.status, IncomingVideo.Status.REGISTERED)

    def test_cancel_removes_everything(self):
        data = self.video_bytes()
        up = self.start(data).json()
        self.put(up["id"], data[:1000], 0)
        self.client.post(up["form_url"], self.form_data())
        inbox = self.client.get(reverse("intake:inbox"))
        self.assertContains(inbox, "Uploads in progress")
        self.client.post(reverse("intake:upload_cancel", args=[up["id"]]))
        self.assertFalse(IncomingVideo.objects.exists())
        self.assertFalse(Recording.all_objects.exists())
        self.assertFalse(list((self.root / "incoming" / "rig1").iterdir()))

    def test_refused(self):
        data = b"x" * 100
        self.assertEqual(self.start(data, name="notes.txt").status_code, 409)
        r = self.start(data, name="video.mp4")                          # no camera in the name, no rig chosen
        self.assertContains(r, "Choose the rig", status_code=409)
        self.assertEqual(self.start(data, name="video.mp4", rig="2").json()["rig"], "R2")
        self.drop(name="Basler acA4024-29um (23517383)_20260203_120000000.mp4")
        r = self.start(data, name="Basler acA4024-29um (23517383)_20260203_120000000.mp4")
        self.assertContains(r, "already waiting", status_code=409)
        up = self.start(data).json()
        self.assertContains(self.start(b"y" * 99), "Another file", status_code=409)
        self.assertEqual(self.put(up["id"], b"z" * 200, 0).status_code, 409)    # more than announced
