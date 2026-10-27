"""
Domain model for the Drosophila lab data system.

Mirrors what the lab records per video (the old metadata GUI), plus:
per-arena analysis windows, events (divider removed, vapor puff), and
per-side groups in wall arenas.

Nothing here knows about processing. Video properties (fps, resolution,
duration, checksum) belong to the video asset, not to the recording.

Rules: codes never change; times are stored in UTC; history is kept; deletes are soft.
"""

from __future__ import annotations

import datetime as dt

import jsonschema
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models, transaction
from django.utils import timezone
from simple_history.models import HistoricalRecords

CODE = RegexValidator(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$", "Use letters, digits and _ . + - only.")


def validate_json(schema: dict, value, field: str):
    if not schema:
        return
    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError as e:
        path = ".".join(str(p) for p in e.absolute_path)
        raise ValidationError({field: f"{path + ': ' if path else ''}{e.message}"})


def check_window(start, end, field: str):
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
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now, editable=False)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = AliveManager()
    all_objects = AliveQuerySet.as_manager()
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
    MALE = "M", "Male"
    FEMALE = "F", "Female"
    MIX = "X", "Mix"


# ---------------------------------------------------------------------------
# Catalogues (the drop-down lists)
# ---------------------------------------------------------------------------

class Genotype(DomainModel):
    code = models.CharField(max_length=64, unique=True, validators=[CODE])   # "CS"
    name = models.CharField(max_length=200, blank=True)                     # "Canton-S"
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Condition(DomainModel):
    code = models.CharField(max_length=64, unique=True, validators=[CODE])   # "Control", "Iso7d", "Cocaine75"
    genotype = models.ForeignKey(Genotype, on_delete=models.PROTECT)
    description = models.TextField(blank=True, help_text="What was done to the flies: housing, duration, drug, dose.")

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Protocol(DomainModel):
    code = models.CharField(max_length=64, unique=True, validators=[CODE])
    name = models.CharField(max_length=200, blank=True)
    duration_s = models.PositiveIntegerField(null=True, blank=True,
                                             help_text="Expected analysed length in seconds (30 min = 1800).")
    description = models.TextField(blank=True)
    url = models.URLField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Rig(DomainModel):
    code = models.CharField(max_length=16, unique=True, validators=[CODE])   # "R1"
    incoming_dir = models.CharField(max_length=32, blank=True, validators=[CODE],
                                    help_text='Folder under incoming/ where this rig drops videos, e.g. "rig1".')
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Camera(DomainModel):
    code = models.CharField(max_length=16, unique=True, validators=[CODE])   # "1", "2"
    model = models.CharField(max_length=128, blank=True)                    # "acA4024-29um"
    serial = models.CharField(max_length=64, blank=True, help_text="Read from video file names.")
    usual_rig = models.ForeignKey(Rig, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                  help_text="Where this camera normally sits; a mismatch triggers a warning.")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["serial"], condition=~models.Q(serial=""),
                                               name="unique_camera_serial")]

    def __str__(self):
        return self.code


ZONES_SCHEMA = {
    "type": "object",
    "additionalProperties": {"type": "array", "minItems": 3,
                             "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}},
}


class ArenaLayout(DomainModel):
    """Arenas in the camera view. Numbering: A01 top-left, then left to right, row by row."""

    code = models.CharField(max_length=32, unique=True, validators=[CODE])   # "2x2", "3x4", "wall-2x2"
    rows = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    cols = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    arena_diameter_mm = models.FloatField(null=True, blank=True)
    zones = models.JSONField(
        default=dict, blank=True,
        help_text='Empty for normal arenas. Wall arena: zone name -> polygon in mm, e.g. '
                  '{"left": [[0,0],[20,0],[20,40],[0,40]], "right": [[20,0],[40,0],[40,40],[20,40]]}')
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code

    def clean(self):
        validate_json(ZONES_SCHEMA, self.zones, "zones")
        if "full" in self.zones:
            raise ValidationError({"zones": '"full" is reserved for arenas without zones.'})

    @property
    def n_arenas(self) -> int:
        return self.rows * self.cols

    def position(self, index: int) -> tuple[int, int]:
        """(row, col), 1-based."""
        if not 1 <= index <= self.n_arenas:
            raise ValueError(f"Arena {index} is outside 1..{self.n_arenas}.")
        return (index - 1) // self.cols + 1, (index - 1) % self.cols + 1

    def zone_names(self) -> list[str]:
        return list(self.zones) or ["full"]


class EventKind(DomainModel):
    code = models.CharField(max_length=64, unique=True, validators=[CODE])   # "divider_removed", "vapor_puff"
    name = models.CharField(max_length=200)
    is_interval = models.BooleanField(default=False, help_text="Interval events need a duration.")
    param_schema = models.JSONField(default=dict, blank=True, help_text="JSON Schema for Event.params.")
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Experiment(DomainModel):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        RUNNING = "running", "Running"
        DONE = "done", "Done"

    code = models.CharField(max_length=64, unique=True, validators=[CODE])
    name = models.CharField(max_length=200)
    aim = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    start_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "code"]

    def __str__(self):
        return self.code


# ---------------------------------------------------------------------------
# Recording, arenas, fly groups, events
# ---------------------------------------------------------------------------

class Recording(DomainModel):
    class Status(models.TextChoices):
        RECORDED = "recorded", "Recorded"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    code = models.CharField(max_length=32, unique=True, editable=False)     # R1-20261022-003
    experiment = models.ForeignKey(Experiment, on_delete=models.PROTECT, related_name="recordings")
    protocol = models.ForeignKey(Protocol, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+", help_text="Scientist who recorded.")
    rig = models.ForeignKey(Rig, on_delete=models.PROTECT, related_name="recordings")
    camera = models.ForeignKey(Camera, on_delete=models.PROTECT, related_name="recordings")
    layout = models.ForeignKey(ArenaLayout, on_delete=models.PROTECT, related_name="recordings")
    started_at = models.DateTimeField(help_text="Insertion date and time.")

    # flies: defaults copied to every arena (and zone)
    condition = models.ForeignKey(Condition, on_delete=models.PROTECT, related_name="+")
    sex = models.CharField(max_length=1, choices=Sex.choices, default=Sex.MALE)
    n_flies = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Flies per arena (per zone).")
    cross_date = models.DateField(null=True, blank=True)
    sorting_date = models.DateField(null=True, blank=True)
    incubator = models.CharField(max_length=32, blank=True)

    # analysis window copied to every arena; seconds from video start (10 min = 600)
    window_start_s = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    window_end_s = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECORDED)
    rejection_reason = models.TextField(blank=True)
    technical_notes = models.TextField(blank=True)
    behavior_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return self.code or "(new recording)"

    def clean(self):
        check_window(self.window_start_s, self.window_end_s, "window_end_s")
        if self.cross_date and self.sorting_date and self.sorting_date < self.cross_date:
            raise ValidationError({"sorting_date": "Sorting cannot be before the cross."})
        if self.status == self.Status.REJECTED and not self.rejection_reason:
            raise ValidationError({"rejection_reason": "Give a reason for rejecting the recording."})

    def save(self, *args, **kwargs):
        if self.code:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            Rig.objects.select_for_update().get(pk=self.rig_id)
            self.code = self._next_code()
            super().save(*args, **kwargs)

    def _next_code(self) -> str:
        day = self.started_at.astimezone(dt.timezone.utc).strftime("%Y%m%d")
        prefix = f"{self.rig.code}-{day}-"
        last = (Recording.all_objects.filter(code__startswith=prefix)   # incl. deleted: never reuse a code
                .order_by("-code").values_list("code", flat=True).first())
        seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
        return f"{prefix}{seq:03d}"

    @property
    def days_since_sorting(self) -> int | None:
        return (self.started_at.date() - self.sorting_date).days if self.sorting_date else None

    @transaction.atomic
    def populate_arenas(self):
        """Create missing arenas, each with one fly group per zone using the recording defaults."""
        for i in range(1, self.layout.n_arenas + 1):
            arena, _ = Arena.objects.get_or_create(
                recording=self, index=i,
                defaults={"window_start_s": self.window_start_s, "window_end_s": self.window_end_s})
            if arena.status != Arena.Status.EMPTY and not arena.fly_groups.exists():
                for zone in self.layout.zone_names():
                    FlyGroup.objects.create(arena=arena, zone=zone, condition=self.condition,
                                            sex=self.sex, n_flies=self.n_flies)


class Arena(DomainModel):
    class Status(models.TextChoices):
        OK = "ok", "Valid"
        EMPTY = "empty", "Empty position"
        EXCLUDED = "excluded", "Not valid"

    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="arenas")
    index = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OK)
    exclusion_reason = models.TextField(blank=True)
    window_start_s = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)],
                                       help_text="Seconds from video start; empty = from the start.")
    window_end_s = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)],
                                     help_text="Empty = to the end.")
    geometry = models.JSONField(default=dict, blank=True, help_text='Pixels: {"circle": [cx, cy, r]}.')
    px_per_mm = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["recording", "index"]
        constraints = [models.UniqueConstraint(fields=["recording", "index"], name="unique_arena_index")]

    def __str__(self):
        return self.code

    @property
    def code(self) -> str:
        return f"{self.recording.code}_A{self.index:02d}"

    @property
    def position(self) -> tuple[int, int]:
        return self.recording.layout.position(self.index)

    @property
    def window(self) -> tuple[float, float | None]:
        return (self.window_start_s or 0.0, self.window_end_s)

    def clean(self):
        if self.recording_id and self.index and self.index > self.recording.layout.n_arenas:
            raise ValidationError({"index": f"Layout has only {self.recording.layout.n_arenas} arenas."})
        check_window(self.window_start_s, self.window_end_s, "window_end_s")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.status == self.Status.EMPTY:
            self.fly_groups.all().delete()


class FlyGroup(DomainModel):
    """Flies in one arena, or in one side of a wall arena."""

    arena = models.ForeignKey(Arena, on_delete=models.CASCADE, related_name="fly_groups")
    zone = models.CharField(max_length=32, default="full")
    condition = models.ForeignKey(Condition, on_delete=models.PROTECT, related_name="fly_groups")
    sex = models.CharField(max_length=1, choices=Sex.choices, default=Sex.MALE)
    n_flies = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["arena", "zone"]

    def __str__(self):
        return f"{self.arena.code}/{self.zone}: {self.condition} {self.sex}"

    def clean(self):
        if not self.arena_id:
            return
        if self.arena.status == Arena.Status.EMPTY:
            raise ValidationError({"arena": "This arena position is empty."})
        allowed = self.arena.recording.layout.zone_names()
        if self.zone not in allowed:
            raise ValidationError({"zone": f"Allowed zones: {', '.join(allowed)}."})


class Event(DomainModel):
    """Something at a time in the video: a point (divider removed) or an interval (vapor puff)."""

    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="events")
    arena = models.ForeignKey(Arena, null=True, blank=True, on_delete=models.CASCADE, related_name="events",
                              help_text="Empty = all arenas.")
    kind = models.ForeignKey(EventKind, on_delete=models.PROTECT)
    t_seconds = models.FloatField(validators=[MinValueValidator(0)], help_text="Seconds from video start.")
    duration_s = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
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
        if self.kind_id:
            if self.kind.is_interval and self.duration_s is None:
                errors["duration_s"] = "This event needs a duration."
            if not self.kind.is_interval and self.duration_s is not None:
                errors["duration_s"] = "This event is a point in time; leave duration empty."
        if errors:
            raise ValidationError(errors)
        if self.kind_id:
            validate_json(self.kind.param_schema, self.params, "params")
