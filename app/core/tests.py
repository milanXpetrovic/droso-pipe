from django.test import TestCase

from .formats import RAW_VIDEO, asset_type, validate_raw_video


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
