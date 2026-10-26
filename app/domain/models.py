"""
Domain model for the Drosophila lab data system.

Describes the world: flies, treatments, recordings, arenas and devices.
Nothing here knows about processing. Derived data (videos, trajectories,
networks, models) are assets in another app that point *to* these entities.

Rules
  - catalogues are tables, not enums in code
  - parameters are validated against a JSON Schema stored on their kind
  - units are explicit (field-name suffix or schema)
  - codes never change; all times are UTC; history is kept; deletes are soft
"""

from __future__ import annotations

import datetime as dt

import jsonschema
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models import Avg, Max, Min
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone
from simple_history.models import HistoricalRecords

CODE = RegexValidator(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$", "Use letters, digits and _ . + - only.")


def validate_json(schema: dict, value, field: str):
    """Validate a JSON value against a JSON Schema; raise a Django ValidationError on failure."""
    if not schema:
        return
    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError as e:
        path = ".".join(str(p) for p in e.absolute_path)
        raise ValidationError({field: f"{path + ': ' if path else ''}{e.message}"})


def check_window(start, end, field: str):
    """Analysis windows are in seconds from the start of the raw video; end must be after start."""
    if start is not None and end is not None and end <= start:
        raise ValidationError({field: "Window end must be after window start."})


# ---------------------------------------------------------------------------
# Base: timestamps, soft delete, history
# ---------------------------------------------------------------------------

class AliveQuerySet(models.QuerySet):
    def delete(self):
        return self.update(deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()


class AliveManager(models.Manager.from_queryset(AliveQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class DomainModel(models.Model):
    # default= instead of auto_now(_add) so that fixtures (raw saves) load cleanly
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now, editable=False)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = AliveManager()                  # hides soft-deleted rows
    all_objects = AliveQuerySet.as_manager()  # includes them
    history = HistoricalRecords(inherit=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.updated_at = timezone.now()
        uf = kwargs.get("update_fields")
        if uf is not None and "updated_at" not in uf:
            kwargs["update_fields"] = [*uf, "updated_at"]
        super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])


class Sex(models.TextChoices):
    FEMALE = "F", "Female"
    MALE = "M", "Male"
    UNKNOWN = "U", "Unknown"


# ---------------------------------------------------------------------------
# Catalogues
# ---------------------------------------------------------------------------

class Genotype(DomainModel):
    code = models.CharField(max_length=64, unique=True, validators=[CODE])   # "CS"
    name = models.CharField(max_length=200, blank=True)                     # "Canton-S"
    source = models.CharField(max_length=200, blank=True)
    stock_id = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class ManipulationKind(DomainModel):
    code = models.CharField(max_length=64, unique=True, validators=[CODE])   # "isolation", "drug"
    name = models.CharField(max_length=200)
    param_schema = models.JSONField(default=dict, blank=True,
                                    help_text="JSON Schema for Manipulation.params of this kind.")
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class EventKind(DomainModel):
    code = models.CharField(max_length=64, unique=True, validators=[CODE])   # "divider_removed", "vapor_puff"
    name = models.CharField(max_length=200)
    is_interval = models.BooleanField(default=False, help_text="Interval events require duration_s.")
    param_schema = models.JSONField(default=dict, blank=True,
                                    help_text="JSON Schema for Event.params of this kind.")
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Rig(DomainModel):
    code = models.CharField(max_length=16, unique=True, validators=[CODE])   # "R1"
    location = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Camera(DomainModel):
    serial = models.CharField(max_length=64, unique=True)
    model = models.CharField(max_length=128)                                 # "Basler acA3800-10gm"
    sensor_width_px = models.PositiveIntegerField(null=True, blank=True)
    sensor_height_px = models.PositiveIntegerField(null=True, blank=True)
    max_fps = models.FloatField(null=True, blank=True)
    lens = models.CharField(max_length=128, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.model} ({self.serial})"


LAYOUT_TEMPLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "arenas": {
            "type": "object",
            "patternProperties": {
                "^[0-9]+$": {
                    "type": "object",
                    "properties": {
                        "zones": {
                            "type": "object",
                            "additionalProperties": {      # zone name -> polygon in mm
                                "type": "array", "minItems": 3,
                                "items": {"type": "array", "items": {"type": "number"},
                                          "minItems": 2, "maxItems": 2},
                            },
                        },
                        "features": {"type": "object"},    # e.g. {"food": [x_mm, y_mm]}
                    },
                    "additionalProperties": False,
                }
            },
            "additionalProperties": False,
        }
    },
    "additionalProperties": False,
}


class ArenaLayout(DomainModel):
    class Shape(models.TextChoices):
        CIRCLE = "circle", "Circle"
        RECT = "rect", "Rectangle"

    code = models.CharField(max_length=32, unique=True, validators=[CODE])   # "4-grid", "wall-4"
    name = models.CharField(max_length=200, blank=True)
    n_arenas = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    shape = models.CharField(max_length=8, choices=Shape.choices, default=Shape.CIRCLE)
    size_x_mm = models.FloatField(null=True, blank=True, help_text="Diameter for circles; width for rectangles.")
    size_y_mm = models.FloatField(null=True, blank=True, help_text="Height for rectangles; empty for circles.")
    template = models.JSONField(
        default=dict, blank=True,
        help_text='Per-arena zones and features in mm, origin at the arena top-left. Example: '
                  '{"arenas": {"1": {"zones": {"left": [[0,0],[20,0],[20,40],[0,40]], '
                  '"right": [[20,0],[40,0],[40,40],[20,40]]}}}}. Arenas without zones have one zone "full".')
    numbering_image = models.FileField(upload_to="layouts/", blank=True,
                                       help_text="Picture showing which arena is A01, A02, ...")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code

    def clean(self):
        validate_json(LAYOUT_TEMPLATE_SCHEMA, self.template, "template")
        for key in self.template.get("arenas", {}):
            if not 1 <= int(key) <= (self.n_arenas or 0):
                raise ValidationError({"template": f"Arena {key} is outside 1..{self.n_arenas}."})

    def zones(self, index: int) -> list[str]:
        z = self.template.get("arenas", {}).get(str(index), {}).get("zones")
        return list(z) if z else ["full"]


class DamMonitor(DomainModel):
    code = models.CharField(max_length=16, unique=True, validators=[CODE])   # "M05"
    model = models.CharField(max_length=32, blank=True)                     # "DAM2", "DAM5H"
    n_channels = models.PositiveSmallIntegerField(default=32)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.code


class Incubator(DomainModel):
    code = models.CharField(max_length=32, unique=True, validators=[CODE])
    location = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.code


class SensorDevice(DomainModel):
    code = models.CharField(max_length=32, unique=True, validators=[CODE])   # "rpi-lab", "incubator-1"
    kind = models.CharField(max_length=64, blank=True)
    rig = models.ForeignKey(Rig, null=True, blank=True, on_delete=models.PROTECT, related_name="sensors")
    incubator = models.ForeignKey(Incubator, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="sensors")
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.code


# ---------------------------------------------------------------------------
# Experimental design
# ---------------------------------------------------------------------------

class Manipulation(DomainModel):
    class Phase(models.TextChoices):
        BEFORE = "before", "Before recording"
        DURING = "during", "During recording"

    code = models.CharField(max_length=64, unique=True, validators=[CODE])   # "iso7d", "coc75"
    kind = models.ForeignKey(ManipulationKind, on_delete=models.PROTECT)
    phase = models.CharField(max_length=8, choices=Phase.choices, default=Phase.BEFORE)
    params = models.JSONField(default=dict, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code

    def clean(self):
        if self.kind_id:
            validate_json(self.kind.param_schema, self.params, "params")


class Condition(DomainModel):
    """One experimental group: genotype + set of manipulations. A combination may exist only once."""

    code = models.CharField(max_length=64, unique=True, validators=[CODE])   # "CS_iso7d"
    genotype = models.ForeignKey(Genotype, on_delete=models.PROTECT)
    manipulations = models.ManyToManyField(Manipulation, blank=True)
    signature = models.CharField(max_length=255, blank=True, editable=False, db_index=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code

    @staticmethod
    def signature_for(genotype_id, manipulation_ids) -> str:
        return f"{genotype_id}:" + ",".join(str(i) for i in sorted(manipulation_ids))

    def refresh_signature(self):
        sig = self.signature_for(self.genotype_id, self.manipulations.values_list("pk", flat=True))
        if sig != self.signature:
            type(self).all_objects.filter(pk=self.pk).update(signature=sig)
            self.signature = sig

    def duplicates(self, genotype_id, manipulation_ids) -> models.QuerySet:
        sig = self.signature_for(genotype_id, manipulation_ids)
        return type(self).objects.filter(signature=sig).exclude(pk=self.pk)


@receiver(m2m_changed, sender=Condition.manipulations.through)
def _condition_manipulations_changed(sender, instance, action, reverse, **kwargs):
    if not reverse and action in ("post_add", "post_remove", "post_clear"):
        instance.refresh_signature()


class Experiment(DomainModel):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        ARCHIVED = "archived", "Archived"

    code = models.CharField(max_length=64, unique=True, validators=[CODE])
    name = models.CharField(max_length=200)
    aim = models.TextField(blank=True)
    protocol = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "code"]

    def __str__(self):
        return self.code


# ---------------------------------------------------------------------------
# Flies
# ---------------------------------------------------------------------------

class Cohort(DomainModel):
    """Flies of common origin (same collection, eclosion window). Later split into conditions;
    some go to DAM, some to arenas."""

    code = models.CharField(max_length=64, unique=True, validators=[CODE])   # "CS-20261015"
    genotype = models.ForeignKey(Genotype, on_delete=models.PROTECT)
    eclosion_from = models.DateField()
    eclosion_to = models.DateField(null=True, blank=True, help_text="Empty if eclosed on a single day.")
    collected_on = models.DateField(null=True, blank=True)
    source = models.CharField(max_length=200, blank=True)                   # bottle / vial
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-eclosion_from"]

    def __str__(self):
        return self.code

    def clean(self):
        if self.eclosion_from and self.eclosion_to and self.eclosion_to < self.eclosion_from:
            raise ValidationError({"eclosion_to": "Must not be before eclosion_from."})

    def age_days_on(self, day: dt.date) -> tuple[int, int]:
        """(youngest, oldest) age in days on a given date."""
        return (day - (self.eclosion_to or self.eclosion_from)).days, (day - self.eclosion_from).days


# ---------------------------------------------------------------------------
# Recording, arenas, fly groups, events
# ---------------------------------------------------------------------------

class Recording(DomainModel):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        RECORDING = "recording", "Recording"
        RECORDED = "recorded", "Recorded"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    code = models.CharField(max_length=32, unique=True, editable=False)     # R1-20261022-003
    experiment = models.ForeignKey(Experiment, on_delete=models.PROTECT, related_name="recordings")
    rig = models.ForeignKey(Rig, on_delete=models.PROTECT, related_name="recordings")
    camera = models.ForeignKey(Camera, on_delete=models.PROTECT, related_name="recordings")
    layout = models.ForeignKey(ArenaLayout, on_delete=models.PROTECT, related_name="recordings")
    default_condition = models.ForeignKey(Condition, on_delete=models.PROTECT, related_name="+")
    default_cohort = models.ForeignKey(Cohort, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="+")
    started_at = models.DateTimeField()
    planned_duration_s = models.PositiveIntegerField(null=True, blank=True)
    default_window_start_s = models.FloatField(
        null=True, blank=True, validators=[MinValueValidator(0)],
        help_text="Default analysis window copied to new arenas; seconds from video start (10 min = 600).")
    default_window_end_s = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    rejection_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return self.code or "(new recording)"

    def clean(self):
        if self.default_cohort_id and self.default_condition_id and \
                self.default_cohort.genotype_id != self.default_condition.genotype_id:
            raise ValidationError({"default_cohort": "Cohort genotype differs from the condition genotype."})
        if self.status == self.Status.REJECTED and not self.rejection_reason:
            raise ValidationError({"rejection_reason": "Give a reason for rejecting the recording."})
        check_window(self.default_window_start_s, self.default_window_end_s, "default_window_end_s")

    def save(self, *args, **kwargs):
        if self.code:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            Rig.objects.select_for_update().get(pk=self.rig_id)   # serialise code generation per rig
            self.code = self._next_code()
            super().save(*args, **kwargs)

    def _next_code(self) -> str:
        day = self.started_at.astimezone(dt.timezone.utc).strftime("%Y%m%d")
        prefix = f"{self.rig.code}-{day}-"
        last = (Recording.all_objects.filter(code__startswith=prefix)   # incl. deleted: never reuse a code
                .order_by("-code").values_list("code", flat=True).first())
        seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
        return f"{prefix}{seq:03d}"

    @transaction.atomic
    def populate_arenas(self):
        """Create arenas from the layout and one FlyGroup per zone with the default condition and cohort.
        Arenas that already have fly groups are left untouched."""
        for i in range(1, self.layout.n_arenas + 1):
            arena, _ = Arena.objects.get_or_create(
                recording=self, index=i,
                defaults={"window_start_s": self.default_window_start_s, "window_end_s": self.default_window_end_s})
            if not arena.fly_groups.exists():
                for zone in self.layout.zones(i):
                    FlyGroup.objects.create(arena=arena, zone=zone, condition=self.default_condition,
                                            cohort=self.default_cohort)

    def environment(self, duration_s: float | None = None) -> dict | None:
        """Aggregate sensor readings of this rig's devices over the recording interval."""
        duration_s = duration_s or self.planned_duration_s
        if not duration_s:
            return None
        end = self.started_at + dt.timedelta(seconds=duration_s)
        return SensorReading.objects.filter(device__rig=self.rig, ts__gte=self.started_at, ts__lte=end).aggregate(
            temp_mean_c=Avg("temperature_c"), temp_min_c=Min("temperature_c"), temp_max_c=Max("temperature_c"),
            humidity_mean_pct=Avg("humidity_pct"), light_mean_lux=Avg("light_lux"),
        )


class Arena(DomainModel):
    class Status(models.TextChoices):
        OK = "ok", "OK"
        EXCLUDED = "excluded", "Excluded"

    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="arenas")
    index = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    geometry = models.JSONField(default=dict, blank=True,
                                help_text='Pixels in this video: {"circle": [cx, cy, r]}, '
                                          '{"bbox": [x, y, w, h]} or {"polygon": [[x, y], ...]}.')
    px_per_mm = models.FloatField(null=True, blank=True)
    window_start_s = models.FloatField(
        null=True, blank=True, validators=[MinValueValidator(0)],
        help_text="Part of the video used for this arena; seconds from video start. Empty = from the start.")
    window_end_s = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)],
                                     help_text="Empty = to the end of the video.")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OK)
    exclusion_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["recording", "index"]
        constraints = [models.UniqueConstraint(fields=["recording", "index"], name="unique_arena_index")]

    def __str__(self):
        return self.code

    @property
    def code(self) -> str:
        return f"{self.recording.code}_A{self.index:02d}"

    def clean(self):
        if self.recording_id and self.index and self.index > self.recording.layout.n_arenas:
            raise ValidationError({"index": f"Layout has only {self.recording.layout.n_arenas} arenas."})
        if self.status == self.Status.EXCLUDED and not self.exclusion_reason:
            raise ValidationError({"exclusion_reason": "Give a reason for excluding the arena."})
        check_window(self.window_start_s, self.window_end_s, "window_end_s")

    @property
    def window(self) -> tuple[float, float | None]:
        """(start_s, end_s) in raw-video time; end None = to the end of the video."""
        return (self.window_start_s or 0.0, self.window_end_s)


class FlyGroup(DomainModel):
    """Flies of one kind in one zone of an arena. Mixed sexes = one FlyGroup per sex."""

    arena = models.ForeignKey(Arena, on_delete=models.CASCADE, related_name="fly_groups")
    zone = models.CharField(max_length=32, default="full")
    condition = models.ForeignKey(Condition, on_delete=models.PROTECT, related_name="fly_groups")
    cohort = models.ForeignKey(Cohort, null=True, blank=True, on_delete=models.PROTECT, related_name="fly_groups")
    sex = models.CharField(max_length=1, choices=Sex.choices, default=Sex.UNKNOWN)
    n_flies = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["arena", "zone", "sex"]

    def __str__(self):
        return f"{self.arena.code}/{self.zone}: {self.condition} ({self.sex})"

    def clean(self):
        if self.arena_id:
            allowed = self.arena.recording.layout.zones(self.arena.index)
            if self.zone not in allowed:
                raise ValidationError({"zone": f"Allowed zones for this arena: {', '.join(allowed)}."})
        if self.cohort_id and self.condition_id and self.cohort.genotype_id != self.condition.genotype_id:
            raise ValidationError({"cohort": "Cohort genotype differs from the condition genotype."})

    @property
    def age_days(self) -> tuple[int, int] | None:
        if not self.cohort_id:
            return None
        return self.cohort.age_days_on(self.arena.recording.started_at.astimezone(dt.timezone.utc).date())


class Event(DomainModel):
    """Something happening at a time in the video: point (divider removed) or interval (vapor puff)."""

    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="events")
    arena = models.ForeignKey(Arena, null=True, blank=True, on_delete=models.CASCADE, related_name="events",
                              help_text="Empty = applies to the whole recording.")
    zone = models.CharField(max_length=32, blank=True)
    kind = models.ForeignKey(EventKind, on_delete=models.PROTECT)
    t_seconds = models.FloatField(validators=[MinValueValidator(0)], help_text="Seconds from the start of the video.")
    duration_s = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    manipulation = models.ForeignKey(Manipulation, null=True, blank=True, on_delete=models.PROTECT,
                                     help_text="What was delivered, for events such as a vapor puff.")
    params = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["recording", "t_seconds"]

    def __str__(self):
        return f"{self.kind} @ {self.t_seconds:g}s"

    def clean(self):
        errors = {}
        if self.arena_id and self.recording_id and self.arena.recording_id != self.recording_id:
            errors["arena"] = "Arena belongs to a different recording."
        if self.zone:
            if not self.arena_id:
                errors["zone"] = "A zone needs an arena."
            elif self.zone not in self.arena.recording.layout.zones(self.arena.index):
                errors["zone"] = "Zone does not exist in this arena."
        if self.kind_id:
            if self.kind.is_interval and self.duration_s is None:
                errors["duration_s"] = "This event kind is an interval and needs a duration."
            if not self.kind.is_interval and self.duration_s is not None:
                errors["duration_s"] = "This event kind is a point in time; leave duration empty."
        if self.manipulation_id and self.manipulation.phase != Manipulation.Phase.DURING:
            errors["manipulation"] = "Only manipulations with phase 'during' can be attached to an event."
        if errors:
            raise ValidationError(errors)
        if self.kind_id:
            validate_json(self.kind.param_schema, self.params, "params")


# ---------------------------------------------------------------------------
# Support data: DAM and environment sensors
# ---------------------------------------------------------------------------

class DamRun(DomainModel):
    code = models.CharField(max_length=32, unique=True, validators=[CODE])   # "M05-20261015"
    monitor = models.ForeignKey(DamMonitor, on_delete=models.PROTECT, related_name="runs")
    incubator = models.ForeignKey(Incubator, null=True, blank=True, on_delete=models.PROTECT, related_name="dam_runs")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    light_regime = models.CharField(max_length=32, default="LD 12:12")
    temperature_setpoint_c = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return self.code

    def clean(self):
        if self.ended_at and self.started_at and self.ended_at <= self.started_at:
            raise ValidationError({"ended_at": "Must be after started_at."})


class DamChannel(DomainModel):
    class Status(models.TextChoices):
        OK = "ok", "OK"
        DEAD = "dead", "Died during run"
        EXCLUDED = "excluded", "Excluded"

    run = models.ForeignKey(DamRun, on_delete=models.CASCADE, related_name="channels")
    channel = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    condition = models.ForeignKey(Condition, on_delete=models.PROTECT, related_name="dam_channels")
    cohort = models.ForeignKey(Cohort, null=True, blank=True, on_delete=models.PROTECT, related_name="dam_channels")
    sex = models.CharField(max_length=1, choices=Sex.choices, default=Sex.UNKNOWN)
    fly_group = models.ForeignKey(FlyGroup, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="dam_channels",
                                  help_text="Only if this exact fly later went into an arena.")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OK)
    exclusion_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["run", "channel"]
        constraints = [models.UniqueConstraint(fields=["run", "channel"], name="unique_dam_channel")]

    def __str__(self):
        return f"{self.run.code}/ch{self.channel:02d}"

    def clean(self):
        if self.run_id and self.channel and self.channel > self.run.monitor.n_channels:
            raise ValidationError({"channel": f"Monitor has only {self.run.monitor.n_channels} channels."})
        if self.fly_group_id and self.condition_id and self.fly_group.condition_id != self.condition_id:
            raise ValidationError({"fly_group": "Same fly must have the same condition in DAM and in the arena."})
        if self.cohort_id and self.condition_id and self.cohort.genotype_id != self.condition.genotype_id:
            raise ValidationError({"cohort": "Cohort genotype differs from the condition genotype."})
        if self.status == self.Status.EXCLUDED and not self.exclusion_reason:
            raise ValidationError({"exclusion_reason": "Give a reason for excluding the channel."})


class SensorReading(models.Model):
    """High-volume time series: no history, no soft delete."""

    device = models.ForeignKey(SensorDevice, on_delete=models.PROTECT, related_name="readings")
    ts = models.DateTimeField()
    temperature_c = models.FloatField(null=True, blank=True)
    humidity_pct = models.FloatField(null=True, blank=True)
    light_lux = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-ts"]
        constraints = [models.UniqueConstraint(fields=["device", "ts"], name="unique_reading")]

    def __str__(self):
        return f"{self.device} @ {self.ts:%Y-%m-%d %H:%M}"
