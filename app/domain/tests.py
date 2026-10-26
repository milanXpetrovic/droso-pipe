"""Domain model scenarios. Run: python manage.py test domain"""

import datetime as dt

from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import (Arena, ArenaLayout, Camera, Cohort, Condition, DamChannel, DamMonitor, DamRun, Event,
                     EventKind, Experiment, FlyGroup, Genotype, Manipulation, ManipulationKind, Recording, Rig)

T0 = dt.datetime(2026, 10, 22, 9, 30, tzinfo=dt.timezone.utc)


def save(obj):
    obj.full_clean()
    obj.save()
    return obj


class DomainScenarios(TestCase):
    fixtures = ["catalogs"]

    @classmethod
    def setUpTestData(cls):
        cls.CS = Genotype.objects.get(code="CS")
        kind = {k.code: k for k in ManipulationKind.objects.all()}
        cls.rig = save(Rig(code="R1"))
        cls.cam = save(Camera(serial="40012345", model="Basler acA3800-10gm"))
        cls.exp = save(Experiment(code="ISO-2026", name="Isolation vs group"))
        cls.iso7 = save(Manipulation(code="iso7d", kind=kind["isolation"], params={"duration_days": 7}))
        cls.grp7 = save(Manipulation(code="grp7d", kind=kind["group_housing"],
                                     params={"duration_days": 7, "group_size": 20}))
        cls.coc = save(Manipulation(code="coc75-vapor", kind=kind["drug"], phase="during",
                                    params={"substance": "cocaine", "dose": 75, "dose_unit": "ug",
                                            "delivery": "vapor"}))
        cls.c_iso = cls.condition("CS_iso7d", cls.iso7)
        cls.c_grp = cls.condition("CS_grp7d", cls.grp7)
        cls.c_coc = cls.condition("CS_grp7d+coc75", cls.grp7, cls.coc)
        cls.cohort = save(Cohort(code="CS-20261015", genotype=cls.CS, eclosion_from=dt.date(2026, 10, 14),
                                 eclosion_to=dt.date(2026, 10, 15)))
        cls.grid4 = save(ArenaLayout(code="4-grid", n_arenas=4, size_x_mm=40))
        def half(x0): return [[x0, 0], [x0 + 20, 0], [x0 + 20, 40], [x0, 40]]
        cls.wall = save(ArenaLayout(code="wall-1", n_arenas=1, shape="rect", size_x_mm=40, size_y_mm=40,
                                    template={"arenas": {"1": {"zones": {"left": half(0), "right": half(20)}}}}))

    @classmethod
    def condition(cls, code, *manipulations):
        c = save(Condition(code=code, genotype=cls.CS))
        c.manipulations.set(manipulations)
        return c

    def recording(self, layout, condition, **kw):
        rec = save(Recording(experiment=self.exp, rig=self.rig, camera=self.cam, layout=layout,
                             default_condition=condition, default_cohort=self.cohort, started_at=T0, **kw))
        rec.populate_arenas()
        return rec

    def assertInvalid(self, obj, field):
        with self.assertRaises(ValidationError) as ctx:
            obj.full_clean()
        self.assertIn(field, ctx.exception.message_dict)

    # --- design ---------------------------------------------------------------

    def test_manipulation_params_follow_schema(self):
        self.assertInvalid(Manipulation(code="bad", kind=self.iso7.kind, params={"days": 7}), "params")

    def test_condition_combination_is_unique(self):
        self.assertEqual(list(Condition(genotype=self.CS).duplicates(self.CS.pk, [self.grp7.pk])), [self.c_grp])

    # --- scenario 1: 4-grid, isolated vs grouped ------------------------------

    def test_grid_recording(self):
        rec = self.recording(self.grid4, self.c_iso)
        self.assertEqual(rec.code, "R1-20261022-001")
        self.assertEqual(rec.arenas.count(), 4)
        for i in (3, 4):
            g = Arena.objects.get(recording=rec, index=i).fly_groups.get()
            g.condition = self.c_grp
            save(g)
        self.assertEqual(FlyGroup.objects.filter(arena__recording=rec, condition=self.c_grp).count(), 2)
        self.assertEqual(rec.arenas.first().fly_groups.first().age_days, (7, 8))
        self.assertEqual(self.recording(self.grid4, self.c_iso).code, "R1-20261022-002")

    def test_plain_arena_has_only_full_zone(self):
        arena = self.recording(self.grid4, self.c_iso).arenas.first()
        self.assertInvalid(FlyGroup(arena=arena, zone="left", condition=self.c_iso), "zone")

    def test_excluding_arena_needs_reason(self):
        arena = self.recording(self.grid4, self.c_iso).arenas.first()
        arena.status = Arena.Status.EXCLUDED
        self.assertInvalid(arena, "exclusion_reason")

    def test_arena_windows(self):
        rec = self.recording(self.grid4, self.c_iso, default_window_start_s=600, default_window_end_s=2100)
        self.assertEqual([a.window for a in rec.arenas.all()], [(600, 2100)] * 4)
        a2 = rec.arenas.get(index=2)
        a2.window_start_s, a2.window_end_s = 720, 2220
        save(a2)
        self.assertEqual(rec.arenas.get(index=2).window, (720, 2220))
        a2.window_end_s = 500
        self.assertInvalid(a2, "window_end_s")
        whole = self.recording(self.grid4, self.c_iso).arenas.first()
        self.assertEqual(whole.window, (0.0, None))

    # --- scenario 2: wall arena -----------------------------------------------

    def test_wall_arena(self):
        rec = self.recording(self.wall, self.c_grp)
        arena = rec.arenas.get()
        self.assertEqual(sorted(arena.fly_groups.values_list("zone", flat=True)), ["left", "right"])
        right = arena.fly_groups.get(zone="right")
        right.condition, right.sex = self.c_iso, "M"
        save(right)
        save(Event(recording=rec, arena=arena, kind=EventKind.objects.get(code="divider_removed"), t_seconds=300))
        self.assertEqual(rec.events.count(), 1)

    def test_point_event_has_no_duration(self):
        rec = self.recording(self.wall, self.c_grp)
        self.assertInvalid(Event(recording=rec, kind=EventKind.objects.get(code="divider_removed"),
                                 t_seconds=10, duration_s=5), "duration_s")

    def test_layout_template_validated(self):
        self.assertInvalid(ArenaLayout(code="bad", n_arenas=1, template={"arenas": {"2": {}}}), "template")

    # --- scenario 3: vapor puff -----------------------------------------------

    def test_vapor_puff(self):
        rec = self.recording(self.grid4, self.c_coc)
        puff = EventKind.objects.get(code="vapor_puff")
        save(Event(recording=rec, kind=puff, t_seconds=600, duration_s=10, manipulation=self.coc,
                   params={"flow_ml_min": 50}))
        self.assertInvalid(Event(recording=rec, kind=puff, t_seconds=600, manipulation=self.coc), "duration_s")
        self.assertInvalid(Event(recording=rec, kind=puff, t_seconds=600, duration_s=10, manipulation=self.iso7),
                           "manipulation")

    def test_event_arena_must_belong_to_recording(self):
        other = self.recording(self.wall, self.c_grp).arenas.get()
        rec = self.recording(self.grid4, self.c_coc)
        self.assertInvalid(Event(recording=rec, arena=other, kind=EventKind.objects.get(code="note"),
                                 t_seconds=1), "arena")

    # --- scenario 4: DAM from the same cohort ---------------------------------

    def test_dam_run(self):
        run = save(DamRun(code="M05-20261015", monitor=save(DamMonitor(code="M05", n_channels=32)),
                          started_at=dt.datetime(2026, 10, 15, 10, tzinfo=dt.timezone.utc)))
        for ch in range(1, 17):
            save(DamChannel(run=run, channel=ch, condition=self.c_iso, cohort=self.cohort, sex="M"))
        self.assertEqual(run.channels.count(), 16)
        self.assertInvalid(DamChannel(run=run, channel=40, condition=self.c_iso), "channel")
        group = self.recording(self.grid4, self.c_grp).arenas.first().fly_groups.first()
        self.assertInvalid(DamChannel(run=run, channel=20, condition=self.c_iso, fly_group=group), "fly_group")

    # --- soft delete and history ----------------------------------------------

    def test_soft_delete_never_reuses_codes(self):
        first = self.recording(self.grid4, self.c_iso)
        first.delete()
        self.assertFalse(Recording.objects.filter(pk=first.pk).exists())
        self.assertTrue(Recording.all_objects.filter(pk=first.pk).exists())
        self.assertEqual(self.recording(self.grid4, self.c_iso).code, "R1-20261022-002")

    def test_history_is_kept(self):
        rec = self.recording(self.grid4, self.c_iso)
        rec.notes = "camera bumped at 12 min"
        save(rec)
        self.assertEqual(rec.history.count(), 2)
