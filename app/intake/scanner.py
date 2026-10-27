"""Find video files in incoming/<rig.incoming_dir>/ and keep IncomingVideo rows in sync with them."""

import datetime as dt
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from domain.models import Rig

from .models import IncomingVideo
from .naming import parse_basler_name

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov"}


def scan(root: Path | str | None = None, now=None) -> dict:
    root = Path(root or settings.VIDEOS_ROOT)
    now = now or timezone.now()
    counts = {"new": 0, "changed": 0, "missing": 0}
    seen = set()

    for rig in Rig.objects.exclude(incoming_dir=""):
        folder = root / "incoming" / rig.incoming_dir
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if not f.is_file() or f.name.startswith(".") or f.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            rel = f.relative_to(root).as_posix()
            seen.add(rel)
            st = f.stat()
            mtime = dt.datetime.fromtimestamp(st.st_mtime, tz=dt.timezone.utc)
            video = IncomingVideo.objects.filter(rel_path=rel).first()
            if video is None:
                parsed = parse_basler_name(f.name) or {}
                IncomingVideo.objects.create(
                    rel_path=rel, rig=rig, file_name=f.name, size_bytes=st.st_size, mtime=mtime, changed_at=now,
                    camera_model=parsed.get("camera_model", ""), camera_serial=parsed.get("camera_serial", ""),
                    started_at=parsed.get("started_at"))
                counts["new"] += 1
                continue
            fields = []
            if video.size_bytes != st.st_size or video.mtime != mtime:   # still being copied
                video.size_bytes, video.mtime, video.changed_at = st.st_size, mtime, now
                fields += ["size_bytes", "mtime", "changed_at"]
                counts["changed"] += 1
            if video.status == IncomingVideo.Status.MISSING:
                video.status = IncomingVideo.Status.NEW
                fields.append("status")
            if fields:
                video.save(update_fields=fields)

    counts["missing"] = (IncomingVideo.objects.filter(status=IncomingVideo.Status.NEW)
                         .exclude(rel_path__in=seen).update(status=IncomingVideo.Status.MISSING))
    return counts
