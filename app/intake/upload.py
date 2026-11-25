"""Upload from the browser: the video arrives in incoming/<rig>/ in pieces, resumable after any interruption.

    start   -> an IncomingVideo in status "uploading"; the data goes to a hidden incoming/<rig>/.<name>.part
    chunk   -> appended at the offset the server already has (the browser asks again after a failure)
    finish  -> on the last byte: the file gets the time it had on the recording PC (the frame-rate check needs
               it) and its real name; status "new", or "registered" if the form was saved meanwhile.

The scanner ignores hidden files, so a half-uploaded video is never seen as a finished copy.
"""

import datetime as dt
import os
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from domain.models import Camera, Recording, Rig

from .models import IncomingVideo
from .naming import parse_basler_name
from .scanner import VIDEO_EXTENSIONS

PIECE = 1024 * 1024


class UploadError(Exception):
    pass


class OffsetMismatch(UploadError):
    def __init__(self, offset: int):
        super().__init__(f"Server has {offset} bytes.")
        self.offset = offset


def final_path(video: IncomingVideo) -> Path:
    return Path(settings.VIDEOS_ROOT) / video.rel_path


def part_path(video: IncomingVideo) -> Path:
    p = final_path(video)
    return p.with_name(f".{p.name}.part")


def received(video: IncomingVideo) -> int:
    p = part_path(video)
    return p.stat().st_size if p.exists() else 0


def rig_for(name: str, rig_id: str | None) -> Rig:
    """The rig chosen on the page, else the usual rig of the camera in the file name."""
    if rig_id:
        rig = Rig.objects.exclude(incoming_dir="").filter(pk=rig_id).first()
        if rig is None:
            raise UploadError("Unknown rig.")
        return rig
    parsed = parse_basler_name(name) or {}
    camera = Camera.objects.filter(serial=parsed.get("camera_serial") or "-").select_related("usual_rig").first()
    if camera and camera.usual_rig and camera.usual_rig.incoming_dir:
        return camera.usual_rig
    raise UploadError("Cannot tell the rig from the file name. Choose the rig, then drop the video again.")


def start(name: str, size: int, last_modified: dt.datetime, rig: Rig) -> tuple[IncomingVideo, int]:
    """New upload, or the same one again (same name and size): returns it and how many bytes the server has."""
    if Path(name).name != name or name.startswith(".") or Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
        raise UploadError(f"Not a video file name: {name} (expected {', '.join(sorted(VIDEO_EXTENSIONS))}).")
    if size <= 0:
        raise UploadError("The file is empty.")
    rel = f"incoming/{rig.incoming_dir}/{name}"
    with transaction.atomic():
        video = IncomingVideo.objects.select_for_update().filter(rel_path=rel).first()
        if video and video.status == IncomingVideo.Status.UPLOADING:
            if video.size_bytes != size:
                raise UploadError("Another file with this name is being uploaded. Rename one of them.")
            return video, received(video)                                   # resume
        if video and video.status == IncomingVideo.Status.MISSING and not video.recording_id:
            video.delete()                                                  # an old copy that disappeared
        elif video:
            raise UploadError(f"This video is already in the system ({video.get_status_display().lower()}).")
        if (Path(settings.VIDEOS_ROOT) / rel).exists():
            raise UploadError("A file with this name is already waiting in incoming/.")
        parsed = parse_basler_name(name) or {}
        video = IncomingVideo.objects.create(
            rel_path=rel, rig=rig, file_name=name, size_bytes=size, mtime=last_modified, changed_at=timezone.now(),
            status=IncomingVideo.Status.UPLOADING, camera_model=parsed.get("camera_model", ""),
            camera_serial=parsed.get("camera_serial", ""), started_at=parsed.get("started_at"))
    part = part_path(video)
    part.parent.mkdir(parents=True, exist_ok=True)
    part.touch()
    return video, 0


def write_chunk(video_id: int, offset: int, stream, length: int) -> tuple[int, bool]:
    """Append `length` bytes from `stream` at `offset`. Returns (bytes the server has, finished).
    A wrong offset raises OffsetMismatch carrying the right one; the browser simply continues from there."""
    with transaction.atomic():                       # one writer per upload, even with two browser tabs
        video = IncomingVideo.objects.select_for_update().get(pk=video_id)
        if video.status != IncomingVideo.Status.UPLOADING:
            raise UploadError("This upload is finished or was cancelled.")
        have = received(video)
        if offset != have:
            raise OffsetMismatch(have)
        if length <= 0 or have + length > video.size_bytes:
            raise UploadError("Chunk does not fit the announced file size.")
        with open(part_path(video), "ab") as f:
            left = length
            while left:
                piece = stream.read(min(PIECE, left))
                if not piece:
                    break                            # connection lost; what arrived stays, the browser resumes
                f.write(piece)
                left -= len(piece)
        have = received(video)
        if have == video.size_bytes:
            finish(video)
            return have, True
        return have, False


def finish(video: IncomingVideo) -> None:
    """Called inside write_chunk's transaction, with the row locked."""
    part, final = part_path(video), final_path(video)
    if final.exists():
        raise UploadError("A file with this name appeared in incoming/ meanwhile.")
    t = video.mtime.timestamp()
    os.utime(part, (t, t))                           # end of recording, as on the recording PC
    os.replace(part, final)
    video.changed_at = timezone.now() - dt.timedelta(seconds=settings.INCOMING_STABLE_SECONDS)   # complete now
    video.status = IncomingVideo.Status.REGISTERED if video.recording_id else IncomingVideo.Status.NEW
    video.save(update_fields=["changed_at", "status"])


def cancel(video: IncomingVideo) -> None:
    """Stop an upload: the partial file, the row and a recording saved for it are removed."""
    with transaction.atomic():
        video = IncomingVideo.objects.select_for_update().get(pk=video.pk)
        if video.status != IncomingVideo.Status.UPLOADING:
            raise UploadError("Only an upload in progress can be cancelled.")
        part_path(video).unlink(missing_ok=True)
        rec_id = video.recording_id
        video.delete()
        if rec_id:
            Recording.all_objects.filter(pk=rec_id).hard_delete()
