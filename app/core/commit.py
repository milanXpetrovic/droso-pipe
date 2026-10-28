"""
Commit: results a worker left in staging/run-<id>/  ->  immutable assets in derived/<code>/<tool>/<version>-run<id>/.

The worker reported each file's checksum. The NAS copies every file into place as `pipeline`
(read-only), recomputes the checksum during the copy, reads each video's header and frame count,
validates it against its format, and only then registers the assets. Anything wrong: nothing is
registered, everything placed in this attempt is removed, the run is marked failed with the reason.
"""

import datetime as dt
import os
import shutil
from pathlib import PurePosixPath

from django.db import transaction
from django.utils import timezone

from .api import MAX_ATTEMPTS
from .formats import CROP_VIDEO, SPECS, TRAJECTORIES, asset_type, validate_crop_video, validate_trajectories
from .models import Asset, Run
from .storage import copy_with_sha256, resolve, write_json
from .video import count_frames, probe

STALE_AFTER = dt.timedelta(minutes=10)


class CommitError(Exception):
    pass


def requeue_stale(now=None) -> int:
    """Runs whose worker stopped sending heartbeats go back to the queue (or fail after MAX_ATTEMPTS)."""
    now = now or timezone.now()
    n = 0
    for run in Run.objects.filter(status=Run.Status.RUNNING, heartbeat_at__lt=now - STALE_AFTER).exclude(
            tool__code="ingest"):
        if run.attempts < MAX_ATTEMPTS:
            run.status, run.worker, run.error = Run.Status.PENDING, "", "Worker stopped responding."
            run.save(update_fields=["status", "worker", "error"])
        else:
            run.fail("Worker stopped responding.")
        n += 1
    return n


def expected_frames(params: dict, window) -> int | None:
    fps, total = params.get("fps"), params.get("n_frames")
    if not fps:
        return None
    start, end = window or (None, None)
    first = round((start or 0) * fps)
    last = round(end * fps) if end is not None else total
    return (last - first) if last is not None else None


def parse_type(name: str) -> tuple[str, int]:
    code, _, version = name.partition("/")
    key = (code, int(version or 0))
    if key not in SPECS:
        raise CommitError(f"Unknown asset type {name}.")
    return key


def commit_run(run: Run) -> bool:
    rec = run.recording
    staging = resolve(run.staging_rel)
    dest_rel = PurePosixPath("derived", rec.code, run.tool.code, f"{run.tool.version}-run{run.pk}")
    dest = resolve(dest_rel)
    files = {f.get("path"): f for f in run.result.get("files", [])}
    windows = {a["index"]: a.get("window_s") for a in run.params.get("arenas", [])}
    placed, created_dir = [], False
    old_umask = os.umask(0o027)
    try:
        if dest.exists():
            raise CommitError(f"{dest_rel} already exists.")
        dest.mkdir(parents=True)
        created_dir = True
        for name, f in files.items():
            if not name or "/" in name or "\\" in name or name.startswith("."):
                raise CommitError(f"Invalid file name {name!r}.")
            src = staging / name
            if not src.is_file():
                raise CommitError(f"{name} is missing in {run.staging_rel}.")
            part = dest / f".{name}.part"
            sha, size = copy_with_sha256(src, part)
            if sha != f.get("sha256") or size != f.get("size_bytes"):
                part.unlink(missing_ok=True)
                raise CommitError(f"{name}: checksum or size differs from what the worker reported.")
            part.chmod(0o440)
            os.replace(part, dest / name)
            placed.append(dest / name)

        with transaction.atomic():
            assets = []
            for out in run.result.get("outputs", []):
                key, file = parse_type(out.get("type", "")), out.get("file")
                if file not in files:
                    raise CommitError(f"Output {file} was not uploaded.")
                path = dest / file
                meta = dict(out.get("meta", {}))
                arena = rec.arenas.get(index=out["arena"]) if out.get("arena") else None
                warnings = list(meta.pop("tool_warnings", []))
                errors = []
                if key == CROP_VIDEO:
                    meta.update({**probe(path), "n_frames": count_frames(path)})
                    errors, more = validate_crop_video(meta, expected_frames(run.params, windows.get(out.get("arena"))),
                                                       run.params.get("fps"))
                    warnings += more
                elif key == TRAJECTORIES:
                    errors, more, extra = validate_trajectories(path, run.params.get("fps"), run.params.get("n_flies"))
                    warnings += more
                    meta.update(extra)
                elif path.stat().st_size == 0:
                    errors = ["empty file"]
                if errors:
                    raise CommitError(f"{file}: " + "; ".join(errors))
                assets.append(Asset.objects.create(
                    type=asset_type(key), rel_path=str(dest_rel / file), sha256=files[file]["sha256"],
                    size_bytes=files[file]["size_bytes"], meta=meta, warnings=warnings, recording=rec, arena=arena,
                    produced_by=run))
            write_json(dest / "manifest.json", {
                "run": {"id": run.pk, "tool": str(run.tool), "worker": run.worker, "params": run.params,
                        "inputs": [{"id": a.pk, "rel_path": a.rel_path, "sha256": a.sha256} for a in run.inputs.all()]},
                "recording": rec.code,
                "assets": [{"id": a.pk, "type": str(a.type), "file": PurePosixPath(a.rel_path).name, "sha256": a.sha256,
                            "arena": a.arena.index if a.arena else None, "meta": a.meta, "warnings": a.warnings}
                           for a in assets],
            })
            run.status, run.finished_at = Run.Status.DONE, timezone.now()
            run.save(update_fields=["status", "finished_at"])
    except Exception as e:  # noqa: BLE001 - every failure is recorded on the run
        for p in placed:
            p.chmod(0o640)
            p.unlink(missing_ok=True)
        if created_dir:
            shutil.rmtree(dest, ignore_errors=True)
        run.fail(f"Commit failed: {e}", log=run.log)
        return False
    finally:
        os.umask(old_umask)
    shutil.rmtree(staging, ignore_errors=True)       # best effort; leftovers are harmless
    return True


def commit_pending() -> int:
    n = 0
    for run in Run.objects.filter(status=Run.Status.COMMITTING).select_related("tool", "recording"):
        commit_run(run)
        n += 1
    return n
