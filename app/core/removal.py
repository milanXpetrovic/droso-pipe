"""Deleting a recording completely: only for test recordings and uploads made by mistake.

Everything goes: reviews, results, runs, the incoming-video row, arenas, fly groups, events, the recording,
and on disk its raw/ folder, derived/<code>/, staging/run-<id>/ and frame previews. Refused while a machine
is working on it, so no worker hands results back for a recording that no longer exists.
"""

import shutil
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import transaction

from domain.models import Arena, Event, FlyGroup, Recording

from .models import Asset, Review, Run, TrackerTemplate
from .storage import resolve


class RemovalError(Exception):
    pass


def delete_recording(rec: Recording) -> dict:
    """Returns what was deleted (counts and folders)."""
    from intake.models import IncomingVideo
    busy = rec.runs.filter(status__in=[Run.Status.RUNNING, Run.Status.COMMITTING])
    if busy.exists():
        raise RemovalError(f"{busy.first().worker or 'A machine'} is working on this recording right now. "
                           "Try again when it has finished.")
    assets = Asset.objects.filter(recording=rec)
    if TrackerTemplate.objects.filter(asset__in=assets).exists():
        raise RemovalError("A tracker template was made from this recording; replace it first.")

    folders = {resolve(f"derived/{rec.code}")}
    for a in assets.filter(type__code="raw_video"):
        folders.add(resolve(PurePosixPath(a.rel_path).parent))      # raw/YYYY/MM/<code>/
    runs = list(rec.runs.all())
    folders |= {resolve(r.staging_rel) for r in runs}
    previews = Path(settings.MEDIA_ROOT) / "previews"
    folders |= {previews / f"crop-{a.pk}" for a in assets.filter(type__code="crop_video")}
    video = IncomingVideo.objects.filter(recording=rec).first()
    if video:
        folders.add(previews / str(video.pk))

    with transaction.atomic():
        counts = {"reviews": Review.objects.filter(asset__in=assets).delete()[0],
                  "files": assets.count(), "jobs": len(runs)}
        for r in runs:
            r.inputs.clear()
        assets.delete()
        Run.objects.filter(pk__in=[r.pk for r in runs]).delete()
        if video:
            if video.status == IncomingVideo.Status.UPLOADING:
                from intake.upload import part_path
                part_path(video).unlink(missing_ok=True)
            elif video.status != IncomingVideo.Status.INGESTED:
                resolve(video.rel_path).unlink(missing_ok=True)       # still in incoming/, not copied to raw/ yet
            video.delete()
        Event.all_objects.filter(recording=rec).hard_delete()
        FlyGroup.all_objects.filter(arena__recording=rec).hard_delete()
        Arena.all_objects.filter(recording=rec).hard_delete()
        Recording.all_objects.filter(pk=rec.pk).hard_delete()

    removed = []
    for folder in folders:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(str(folder))
    return {**counts, "folders": sorted(removed)}
