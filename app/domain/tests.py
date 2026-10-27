"""Domain scenarios. Run: python manage.py test domain"""

import datetime as dt

from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import (Arena, ArenaLayout, Camera, Condition, Event, EventKind, Experiment, FlyGroup, Genotype,
                     Recording, Rig)

T0 = dt.datetime(2026, 10, 22, 9, 30, tzinfo=dt.timezone.utc)
def HALF(x0): return [[x0, 0], [x0 + 20, 0], [x0 + 20, 40], [x0, 40]]


def save(obj):
    obj.full_clean()
    obj.save()
    return obj


class Scenarios(TestCase):
    fixtures = ["catalogs"]

    @classmethod
    def setUpTestData(cls):
        CS = Genotype.objects.get(code="CS")
        cls.rig, cls.cam = save(Rig(code="R1")), save(Camera(code="1", model="Basler acA3800-10gm"))
        cls.exp = save(Experiment(code="ISO-2026", name="Isolation"))
        cls.ctrl = save(Condition(code="Control", genotype=CS))
        cls.iso = save(Condition(code="Iso7d", genotype=CS, description="Isolated 7 days"))
        cls.g2x2 = save(ArenaLayout(code="2x2", rows=2, cols=2, arena_diameter_mm=40))
        cls.g3x4 = save(ArenaLayout(code="3x4", rows=3, cols=4, arena_diameter_mm=40))
        cls.wall = save(ArenaLayout(code="wall-2x2", rows=2, cols=2, zones={"left": HALF(0), "right": HALF(20)}))

    def recording(self, layout, condition, **kw):
        rec = save(Recording(experiment=self.exp, rig=self.rig, camera=self.cam, layout=layout,
                             condition=condition, started_at=T0, n_flies=12, **kw))
        rec.populate_arenas()
        return rec

    def assertInvalid(self, obj, field):
        with self.assertRaises(ValidationError) as ctx:
            obj.full_clean()
        self.assertIn(field, ctx.exception.message_dict)

    def test_like_old_gui_2x2(self):
        rec = self.recording(self.g2x2, self.ctrl, sex="M", sorting_date=dt.date(2026, 10, 17))
        self.assertEqual(rec.code, "R1-20261022-001")
        self.assertEqual(rec.days_since_sorting, 5)
        self.assertEqual([(g.condition.code, g.sex, g.n_flies) for g in FlyGroup.objects.filter(arena__recording=rec)],
                         [("Control", "M", 12)] * 4)
        a3 = rec.arenas.get(index=3)
        a3.status = Arena.Status.EXCLUDED          # "Arena 3 Valid" unticked; reason optional
        save(a3)
        self.assertEqual(rec.arenas.filter(status="ok").count(), 3)

    def test_3x4_numbering_and_empty_positions(self):
        rec = self.recording(self.g3x4, self.iso)
        self.assertEqual([rec.arenas.get(index=i).position for i in (1, 4, 5, 12)], [(1, 1), (1, 4), (2, 1), (3, 4)])
        for i in (11, 12):
            a = rec.arenas.get(index=i)
            a.status = Arena.Status.EMPTY
            save(a)
        self.assertEqual(FlyGroup.objects.filter(arena__recording=rec).count(), 10)
        rec.populate_arenas()
        self.assertEqual(FlyGroup.objects.filter(arena__recording=rec).count(), 10)
        self.assertInvalid(FlyGroup(arena=rec.arenas.get(index=12), condition=self.iso), "arena")

    def test_wall_arena_sides(self):
        rec = self.recording(self.wall, self.ctrl)
        a1 = rec.arenas.get(index=1)
        self.assertEqual(sorted(a1.fly_groups.values_list("zone", flat=True)), ["left", "right"])
        right = a1.fly_groups.get(zone="right")
        right.condition, right.sex = self.iso, "F"
        save(right)
        save(Event(recording=rec, kind=EventKind.objects.get(code="divider_removed"), t_seconds=300))
        self.assertInvalid(FlyGroup(arena=rec.arenas.get(index=2), zone="full", condition=self.ctrl), "zone")

    def test_vapor_puff(self):
        rec = self.recording(self.g2x2, self.ctrl)
        puff = EventKind.objects.get(code="vapor_puff")
        save(Event(recording=rec, kind=puff, t_seconds=600, duration_s=10,
                   params={"substance": "cocaine", "dose": 75, "dose_unit": "ug"}))
        self.assertInvalid(Event(recording=rec, kind=puff, t_seconds=600,
                                 params={"substance": "cocaine", "dose": 75, "dose_unit": "ug"}), "duration_s")
        self.assertInvalid(Event(recording=rec, kind=puff, t_seconds=600, duration_s=10,
                                 params={"substance": "cocaine"}), "params")

    def test_windows(self):
        rec = self.recording(self.g2x2, self.ctrl, window_start_s=600, window_end_s=2100)
        self.assertEqual([a.window for a in rec.arenas.all()], [(600, 2100)] * 4)
        a2 = rec.arenas.get(index=2)
        a2.window_start_s, a2.window_end_s = 720, 2220
        save(a2)
        a2.window_end_s = 500
        self.assertInvalid(a2, "window_end_s")
        self.assertEqual(self.recording(self.g2x2, self.ctrl).arenas.first().window, (0.0, None))

    def test_codes_history_soft_delete(self):
        first = self.recording(self.g2x2, self.ctrl)
        first.technical_notes = "camera bumped at 12 min"
        save(first)
        self.assertEqual(first.history.count(), 2)
        first.delete()
        self.assertFalse(Recording.objects.filter(pk=first.pk).exists())
        self.assertEqual(self.recording(self.g2x2, self.ctrl).code, "R1-20261022-002")

    def test_dates_and_layout_validated(self):
        self.assertInvalid(Recording(experiment=self.exp, rig=self.rig, camera=self.cam, layout=self.g2x2,
                                     condition=self.ctrl, started_at=T0, cross_date=dt.date(2026, 10, 10),
                                     sorting_date=dt.date(2026, 10, 1)), "sorting_date")
        self.assertInvalid(ArenaLayout(code="bad", rows=1, cols=2, zones={"left": [[0, 0], [1, 1]]}), "zones")


class LabFixture(TestCase):
    """loaddata skips validation, so validate every fixture object here."""
    fixtures = ["catalogs", "lab"]

    def test_lab_fixture_is_valid(self):
        from . import models as m
        for model in (m.Genotype, m.EventKind, m.Rig, m.Camera, m.ArenaLayout, m.Condition):
            for obj in model.objects.all():
                obj.full_clean()
