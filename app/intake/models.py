from django.conf import settings
from django.db import models
from django.utils import timezone

from domain.models import Camera, Recording, Rig


class IncomingVideo(models.Model):
    """A video file found in incoming/<rig>/. Registered when someone enters its metadata."""

    class Status(models.TextChoices):
        NEW = "new", "Waiting for metadata"
        REGISTERED = "registered", "Registered"
        INGESTED = "ingested", "Ingested"
        IGNORED = "ignored", "Ignored"
        MISSING = "missing", "File disappeared"

    rel_path = models.CharField(max_length=500, unique=True, help_text="Relative to the videos root.")
    rig = models.ForeignKey(Rig, on_delete=models.PROTECT, related_name="incoming_videos")
    file_name = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField()
    mtime = models.DateTimeField()
    changed_at = models.DateTimeField(help_text="Last time size or mtime changed; used to detect finished copies.")
    first_seen_at = models.DateTimeField(default=timezone.now)

    # parsed from the file name (empty if the name does not follow the Basler pattern)
    camera_model = models.CharField(max_length=128, blank=True)
    camera_serial = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    # read from the video header when first needed
    duration_s = models.FloatField(null=True, blank=True)
    fps = models.FloatField(null=True, blank=True, help_text="As declared by the file.")
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    codec = models.CharField(max_length=32, blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    recording = models.OneToOneField(Recording, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="incoming_video")
    ignore_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-first_seen_at"]

    def __str__(self):
        return self.rel_path

    def is_stable(self, now=None) -> bool:
        now = now or timezone.now()
        return (now - self.changed_at).total_seconds() >= settings.INCOMING_STABLE_SECONDS

    def ensure_probe(self) -> None:
        """Read duration, fps, resolution and codec once; cached on the row."""
        if self.duration_s is not None:
            return
        from .preview import probe, video_path
        info = probe(video_path(self.rel_path))
        if info["duration_s"] is None:
            return
        for field, value in info.items():
            setattr(self, field, value)
        self.save(update_fields=list(info))

    def ensure_duration(self) -> float | None:
        self.ensure_probe()
        return self.duration_s

    @property
    def n_frames_expected(self) -> int | None:
        return round(self.duration_s * self.fps) if self.duration_s and self.fps else None

    @property
    def camera(self) -> Camera | None:
        if not self.camera_serial:
            return None
        return Camera.objects.filter(serial=self.camera_serial).first()
