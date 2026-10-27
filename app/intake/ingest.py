"""
Ingest: registered video in incoming/<rig>/  ->  immutable raw_video asset in raw/YYYY/MM/<code>/.

Steps (any failure leaves the source untouched and records the error):
  1. copy to a hidden .part file while computing sha256
  2. re-read the copy and compare checksums
  3. read the header (fps, size, duration) and count the frames
  4. validate; inconsistent frame counts become warnings on the asset
  5. make the file read-only and rename it into place
  6. write recording.json and manifest.json, register the asset and the run
  7. only then delete the source from incoming/
"""

import os
import socket
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import transaction

from core.formats import RAW_VIDEO, asset_type, validate_raw_video
from core.models import Asset, Run, Tool
from core.sidecar import recording_sidecar
from core.storage import copy_with_sha256, sha256_of, write_json  # noqa: F401

from .models import IncomingVideo
from .preview import count_frames, probe, video_path

VERSION = "1.0.0"
MAX_ATTEMPTS = 3


class IngestError(Exception):
    pass


def tool() -> Tool:
    t, _ = Tool.objects.get_or_create(code="ingest", version=VERSION, defaults={
        "description": "Copy a registered video from incoming/ to raw/, verify it and register it as raw_video/1.",
        "spec": {"inputs": {}, "outputs": {"video": "raw_video/1"}}})
    return t


def raw_dir(recording) -> PurePosixPath:
    import datetime as dt
    t = recording.started_at.astimezone(dt.timezone.utc)
    return PurePosixPath("raw", f"{t:%Y}", f"{t:%m}", recording.code)


def claim_next() -> IncomingVideo | None:
    with transaction.atomic():
        video = (IncomingVideo.objects.select_for_update(skip_locked=True)
                 .filter(status=IncomingVideo.Status.REGISTERED, ingest_attempts__lt=MAX_ATTEMPTS)
                 .order_by("first_seen_at").first())
        if video:
            video.status = IncomingVideo.Status.INGESTING
            video.save(update_fields=["status"])
        return video


def recover_interrupted():
    """After a restart: videos left half-ingested go back to the queue, their runs are marked failed."""
    IncomingVideo.objects.filter(status=IncomingVideo.Status.INGESTING).update(status=IncomingVideo.Status.REGISTERED)
    for run in Run.objects.filter(tool__code="ingest", status=Run.Status.RUNNING):
        run.fail("Interrupted (process restarted).")


def ingest_one(video: IncomingVideo) -> Asset | None:
    root = Path(settings.VIDEOS_ROOT)
    rec = video.recording
    src = video_path(video.rel_path)
    rel_dir = raw_dir(rec)
    dest_dir = root / rel_dir
    dest = dest_dir / f"video{src.suffix.lower()}"
    part = dest_dir / f".{dest.name}.part"
    run = Run.objects.create(tool=tool(), recording=rec, params={"source": video.rel_path})
    run.start(worker=socket.gethostname())
    log, placed = [], False
    old_umask = os.umask(0o027)
    try:
        if not src.is_file():
            raise IngestError(f"Source file not found: {video.rel_path}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            raise IngestError(f"{rel_dir / dest.name} already exists; raw files are never overwritten.")

        sha, size = copy_with_sha256(src, part)
        log.append(f"copied {size} bytes, sha256 {sha}")
        if size != src.stat().st_size:
            raise IngestError("Source changed size during the copy.")
        if sha256_of(part) != sha:
            raise IngestError("Copy verification failed: checksums differ.")
        log.append("copy verified")

        info = probe(part)
        n_frames = count_frames(part)
        meta = {**info, "n_frames": n_frames, "source_file_name": video.file_name,
                "camera_serial": video.camera_serial or None,
                "measured_fps": (n_frames / info["duration_s"]) if n_frames and info["duration_s"] else None}
        errors, warnings = validate_raw_video(meta)
        if errors:
            raise IngestError("Not a valid video: " + "; ".join(errors))
        log.append(f"{n_frames} frames, {info['fps']} fps declared, {meta['measured_fps']:.3f} fps measured")

        part.chmod(0o440)
        os.replace(part, dest)
        placed = True
        write_json(dest_dir / "recording.json", recording_sidecar(rec))

        with transaction.atomic():
            asset = Asset.objects.create(type=asset_type(RAW_VIDEO), rel_path=str(rel_dir / dest.name), sha256=sha,
                                         size_bytes=size, meta=meta, warnings=warnings, recording=rec,
                                         produced_by=run)
            write_json(dest_dir / "manifest.json", {
                "asset": {"id": asset.pk, "type": str(asset.type), "file": dest.name, "sha256": sha,
                          "size_bytes": size, "meta": meta, "warnings": warnings},
                "recording": rec.code,
                "run": {"id": run.pk, "tool": str(run.tool), "worker": run.worker, "source": video.rel_path},
            })
            video.status, video.ingest_error = IncomingVideo.Status.INGESTED, ""
            video.save(update_fields=["status", "ingest_error"])
            run.finish(log="\n".join(log))
        from core.rules import plan_runs
        plan_runs()                                # e.g. the crop run for this video
    except Exception as e:  # noqa: BLE001 - every failure must be recorded, never lost
        part.unlink(missing_ok=True)
        if placed and not Asset.objects.filter(rel_path=str(rel_dir / dest.name)).exists():
            dest.chmod(0o640)
            dest.unlink(missing_ok=True)           # we placed it in this attempt; nothing refers to it
        msg = str(e) or e.__class__.__name__
        run.fail(msg, log="\n".join(log))
        video.status, video.ingest_error = IncomingVideo.Status.REGISTERED, msg
        video.ingest_attempts += 1
        video.save(update_fields=["status", "ingest_error", "ingest_attempts"])
        return None
    finally:
        os.umask(old_umask)

    try:
        src.unlink()                               # only after the asset is safely registered
    except OSError as e:
        run.log += f"\nwarning: could not remove source: {e}"
        run.save(update_fields=["log"])
    return asset


def ingest_pending(limit: int | None = None) -> int:
    done = 0
    while (limit is None or done < limit) and (video := claim_next()):
        ingest_one(video)
        done += 1
    return done
