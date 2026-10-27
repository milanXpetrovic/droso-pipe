"""
Core of the data store: what data exists (Asset), of which format (AssetType),
which program made it (Tool) and how (Run).

Rules
  - assets are immutable: path, checksum and size never change after creation
  - every asset is produced by a run; a run records tool version, parameters and inputs
  - formats and tools are versioned; a new meaning is a new version, never an edit
"""

from django.db import models
from django.utils import timezone

from domain.models import Arena, Recording


class AssetType(models.Model):
    """A data format with a version, e.g. raw_video/1, trajectories/1."""

    code = models.CharField(max_length=64)
    version = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=200, blank=True)
    spec = models.JSONField(default=dict, blank=True, help_text="What a valid asset of this type looks like.")

    class Meta:
        ordering = ["code", "version"]
        constraints = [models.UniqueConstraint(fields=["code", "version"], name="unique_asset_type")]

    def __str__(self):
        return f"{self.code}/{self.version}"


class Tool(models.Model):
    """A program with a version, e.g. ingest@1.0.0, flytracker@0.6."""

    code = models.CharField(max_length=64)
    version = models.CharField(max_length=32)
    description = models.TextField(blank=True)
    spec = models.JSONField(default=dict, blank=True, help_text="Inputs, outputs, parameters (tool.yaml).")

    class Meta:
        ordering = ["code", "version"]
        constraints = [models.UniqueConstraint(fields=["code", "version"], name="unique_tool")]

    def __str__(self):
        return f"{self.code}@{self.version}"


class Run(models.Model):
    """One execution of a tool on concrete inputs with concrete parameters."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    tool = models.ForeignKey(Tool, on_delete=models.PROTECT, related_name="runs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    params = models.JSONField(default=dict, blank=True)
    inputs = models.ManyToManyField("Asset", blank=True, related_name="consumed_by")
    recording = models.ForeignKey(Recording, null=True, blank=True, on_delete=models.PROTECT, related_name="runs")
    arena = models.ForeignKey(Arena, null=True, blank=True, on_delete=models.PROTECT, related_name="runs")
    worker = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    log = models.TextField(blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "tool"])]

    def __str__(self):
        target = self.arena or self.recording or "—"
        return f"{self.tool} on {target} [{self.status}]"

    def start(self, worker: str = ""):
        self.status, self.worker, self.started_at = self.Status.RUNNING, worker, timezone.now()
        self.save(update_fields=["status", "worker", "started_at"])

    def finish(self, log: str = ""):
        self.status, self.finished_at, self.log = self.Status.DONE, timezone.now(), log
        self.save(update_fields=["status", "finished_at", "log"])

    def fail(self, error: str, log: str = ""):
        self.status, self.finished_at, self.error, self.log = self.Status.FAILED, timezone.now(), error, log
        self.save(update_fields=["status", "finished_at", "error", "log"])


class Asset(models.Model):
    """A file or folder in the store. Immutable once created."""

    class Status(models.TextChoices):
        OK = "ok", "OK"
        STALE = "stale", "Stale (an input or the metadata changed)"

    IMMUTABLE = ("type_id", "rel_path", "sha256", "size_bytes")

    type = models.ForeignKey(AssetType, on_delete=models.PROTECT, related_name="assets")
    rel_path = models.CharField(max_length=500, unique=True, help_text="Relative to the videos root.")
    sha256 = models.CharField(max_length=64)
    size_bytes = models.BigIntegerField()
    meta = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    recording = models.ForeignKey(Recording, null=True, blank=True, on_delete=models.PROTECT, related_name="assets")
    arena = models.ForeignKey(Arena, null=True, blank=True, on_delete=models.PROTECT, related_name="assets")
    produced_by = models.ForeignKey(Run, on_delete=models.PROTECT, related_name="outputs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OK)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["type", "recording"])]

    def __str__(self):
        return f"{self.type}: {self.rel_path}"

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).values(*self.IMMUTABLE).first()
            if old and any(old[f] != getattr(self, f) for f in self.IMMUTABLE):
                raise ValueError("Assets are immutable: type, path, checksum and size cannot change.")
        super().save(*args, **kwargs)
