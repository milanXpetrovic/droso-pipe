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
        self.assertRedirects(r, reverse("intake:inbox"))
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

    def make_video(self, rig="rig1", name=NAME):
        import subprocess
        from .preview import FFMPEG
        out = self.root / "incoming" / rig / name
        subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=10",
                        "-pix_fmt", "yuv420p", "-y", str(out)], check=True)
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
