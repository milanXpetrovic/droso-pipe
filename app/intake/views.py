import random
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from domain.models import Arena, FlyGroup, Protocol, Recording, Rig

from . import upload
from .forms import ArenaFormSet, FlyGroupFormSet, RegisterForm
from .ingest import MAX_ATTEMPTS
from .models import IncomingVideo
from .preview import frame_jpeg, video_path
from .scanner import scan


def preview_context(video: IncomingVideo | None) -> dict:
    """Duration and a starting time (middle of the video) for the frame preview."""
    if video is None:
        return {}
    try:
        duration = video.real_duration()
    except (OSError, ValueError):
        duration = None
    if not duration:
        return {}
    return {"preview_video": video, "duration": duration, "t0": round(duration / 2, 1)}

operator_required = [login_required, permission_required("domain.add_recording", raise_exception=True)]
COPY_FROM_LAST = ["experiment", "protocol", "layout", "condition", "sex", "n_flies", "cross_date", "sorting_date",
                  "incubator", "window_start_s", "window_end_s"]


def operator_view(view):
    for deco in reversed(operator_required):
        view = deco(view)
    return view


@operator_view
def inbox(request):
    scan()
    now = timezone.now()
    pending = list(IncomingVideo.objects.filter(status=IncomingVideo.Status.NEW).select_related("rig"))
    for v in pending:
        v.ready = v.is_stable(now)
        v.known_camera = v.camera
        if v.ready:
            try:
                v.real_length = v.real_duration()          # header read once, then cached on the row
                v.fps_used, v.fps_warning = v.fps_check()
            except (OSError, ValueError):
                pass
    S = IncomingVideo.Status
    done = list(IncomingVideo.objects.filter(status__in=[S.REGISTERED, S.INGESTING, S.INGESTED])
                .select_related("rig", "recording").order_by("-first_seen_at")[:30])
    for v in done:
        v.raw = v.recording.assets.filter(type__code="raw_video").first() if v.status == S.INGESTED else None
        v.runs = list(v.recording.runs.exclude(tool__code="ingest").select_related("tool").order_by("created_at"))
        v.gave_up = v.status == S.REGISTERED and v.ingest_attempts >= MAX_ATTEMPTS
    uploads = list(IncomingVideo.objects.filter(status=S.UPLOADING).select_related("rig", "recording"))
    for v in uploads:
        v.received = upload.received(v)
    return render(request, "intake/inbox.html", {"pending": pending, "done": done, "uploads": uploads})


@operator_view
def register(request, pk):
    video = get_object_or_404(IncomingVideo, pk=pk, status=IncomingVideo.Status.NEW)
    if not video.is_stable():
        messages.warning(request, f"{video.file_name} is still being copied. Try again in a minute.")
        return redirect("intake:inbox")

    camera = video.camera
    if video.camera_serial and camera is None:
        add_url = (reverse("admin:domain_camera_add") +
                   f"?serial={video.camera_serial}&model={video.camera_model}&usual_rig={video.rig_id}")
        return render(request, "intake/unknown_camera.html", {"video": video, "add_url": add_url})

    warnings = []
    try:
        fps_warning = video.fps_check()[1]
    except (OSError, ValueError):
        fps_warning = None
    if fps_warning:
        warnings.append(fps_warning + " Enter all times (analysis window) in real seconds.")
    if camera and camera.usual_rig_id and camera.usual_rig_id != video.rig_id:
        warnings.append(f"Camera {camera} usually sits on {camera.usual_rig}, but this video came from "
                        f"{video.rig}. Were the cameras swapped?")

    instance = Recording(rig=video.rig, camera=camera, operator=request.user, started_at=video.started_at)
    preview = preview_context(video)
    duration = preview.get("duration")
    if request.method == "POST":
        form = RegisterForm(request.POST, instance=instance, camera_from_file=camera,
                            started_from_file=video.started_at, video_duration=duration)
        if form.is_valid():
            recording = save_registration(form, video)
            if recording is None:
                messages.error(request, "Someone else registered this video in the meantime.")
                return redirect("intake:inbox")
            messages.success(request, f"Created {recording.code}. Check the arenas below.")
            return redirect("intake:arenas", code=recording.code)
    else:
        form, last = blank_form(video, instance, camera, duration)
        if last:
            messages.info(request, f"Fields copied from the last recording on {video.rig} ({last.code}).")
    return render(request, "intake/register.html",
                  {"video": video, "form": form, "warnings": warnings, "protocol_durations": protocol_durations(),
                   **preview})


def protocol_durations() -> dict:
    return {str(p.pk): p.duration_s for p in Protocol.objects.exclude(duration_s=None)}


def blank_form(video, instance, camera, duration):
    """The form, filled from the last recording on the same rig (usually the same experiment and flies)."""
    last = Recording.objects.filter(rig=video.rig).order_by("-started_at").first()
    initial = {f: getattr(last, f) for f in COPY_FROM_LAST} if last else {}
    form = RegisterForm(instance=instance, initial=initial, camera_from_file=camera,
                        started_from_file=video.started_at, video_duration=duration)
    return form, last


def save_registration(form, video) -> Recording | None:
    """Recording + arenas for this video. While it is still uploading it stays "uploading" and becomes
    "registered" when the last byte arrives; otherwise it is registered now. None if someone was faster."""
    S = IncomingVideo.Status
    with transaction.atomic():
        locked = IncomingVideo.objects.select_for_update().get(pk=video.pk)
        if locked.status not in (S.NEW, S.UPLOADING) or locked.recording_id:
            return None
        recording = form.save(commit=False)
        if video.started_at:                         # authoritative, keeps milliseconds
            recording.started_at = video.started_at
        recording.save()
        recording.populate_arenas()
        locked.recording = recording
        if locked.status == S.NEW:
            locked.status = S.REGISTERED
        locked.save(update_fields=["recording", "status"])
    return recording


@operator_view
def arenas(request, code):
    recording = get_object_or_404(Recording, code=code)
    video = getattr(recording, "incoming_video", None)
    preview = preview_context(video)
    arena_kwargs = {"form_kwargs": {"video_duration": preview.get("duration")}}
    arena_qs = Arena.objects.filter(recording=recording).order_by("index")
    group_qs = FlyGroup.objects.filter(arena__recording=recording).select_related("arena").order_by("arena__index", "zone")
    if request.method == "POST":
        arena_fs = ArenaFormSet(request.POST, queryset=arena_qs, prefix="arena", **arena_kwargs)
        group_fs = FlyGroupFormSet(request.POST, queryset=group_qs, prefix="group")
        if arena_fs.is_valid() and group_fs.is_valid():
            with transaction.atomic():
                group_fs.save()      # groups first: an arena marked empty then removes its groups
                arena_fs.save()
            messages.success(request, f"{recording.code} saved.")
            return redirect("intake:inbox")
    else:
        arena_fs = ArenaFormSet(queryset=arena_qs, prefix="arena", **arena_kwargs)
        group_fs = FlyGroupFormSet(queryset=group_qs, prefix="group")

    layout = recording.layout
    grid = [[None] * layout.cols for _ in range(layout.rows)]
    for form in arena_fs.forms:
        r, c = layout.position(form.instance.index)
        grid[r - 1][c - 1] = form
    raw = recording.assets.filter(type__code="raw_video").first()
    from core.models import TrackerTemplate
    no_template = (recording.assets.filter(type__code="crop_video").exists()
                   and not TrackerTemplate.objects.filter(layout=recording.layout, active=True).exists())
    runs = list(recording.runs.exclude(tool__code="ingest").select_related("tool").order_by("created_at"))
    for r in runs:
        r.made = list(r.outputs.select_related("arena").order_by("arena__index"))
    return render(request, "intake/arenas.html",
                  {"recording": recording, "arena_fs": arena_fs, "group_fs": group_fs, "grid": grid, "raw": raw, "runs": runs,
                   "no_template": no_template,
                   "protocol_duration": recording.protocol.duration_s if recording.protocol else None, **preview})


@operator_view
def frame(request, pk):
    """JPEG of one frame; ?t=seconds, or a random time if t is missing."""
    video = get_object_or_404(IncomingVideo, pk=pk)
    duration = video.real_duration()
    if not duration:
        raise Http404("Cannot read this video.")
    try:
        t = float(request.GET["t"])                 # real seconds, like every time a person sees
    except (KeyError, ValueError):
        t = random.uniform(0.05, 0.95) * duration
    t = round(min(max(t, 0.0), max(duration - 0.5, 0.0)), 1)
    cache = Path(settings.MEDIA_ROOT) / "previews" / str(video.pk)
    path = frame_jpeg(video_path(video.current_rel_path()), round(video.file_time(t), 2), cache)
    response = FileResponse(open(path, "rb"), content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=86400"
    return response


@operator_view
@require_POST
def ignore(request, pk):
    video = get_object_or_404(IncomingVideo, pk=pk, status=IncomingVideo.Status.NEW)
    video.status = IncomingVideo.Status.IGNORED
    video.ignore_reason = request.POST.get("reason", "").strip()
    video.save(update_fields=["status", "ignore_reason"])
    messages.info(request, f"{video.file_name} ignored.")
    return redirect("intake:inbox")


@operator_view
@require_POST
def retry_ingest(request, pk):
    video = get_object_or_404(IncomingVideo, pk=pk, status=IncomingVideo.Status.REGISTERED)
    video.ingest_attempts, video.ingest_error = 0, ""
    video.save(update_fields=["ingest_attempts", "ingest_error"])
    messages.info(request, f"{video.file_name} will be ingested again.")
    return redirect("intake:inbox")


@operator_view
def tracking(request):
    """All tracked arenas, cleanest first: what to review, and what to retrack with other parameters."""
    from core.models import Asset
    rows = list(Asset.objects.filter(type__code="trajectories").select_related("recording", "arena", "produced_by")
                .order_by("recording__started_at", "arena__index"))
    for a in rows:
        a.flags_per_min = a.meta.get("flags_per_min")
        a.expected = a.produced_by.params.get("n_flies")
    rows.sort(key=lambda a: (a.flags_per_min is None, a.flags_per_min or 0))
    return render(request, "intake/tracking.html", {"rows": rows})


# --- upload from the browser ------------------------------------------------------------------

CHUNK_BYTES = 16 * 1024 * 1024      # short requests (well under gunicorn's timeout even on a slow network)


def upload_json(video, offset, **extra):
    return JsonResponse({"id": video.pk, "offset": offset, "size": video.size_bytes,
                         "chunk_url": reverse("intake:upload_chunk", args=[video.pk]),
                         "form_url": reverse("intake:upload_form", args=[video.pk]),
                         "chunk_bytes": CHUNK_BYTES, **extra})


@operator_view
def upload_page(request):
    return render(request, "intake/upload.html", {"rigs": Rig.objects.exclude(incoming_dir="").order_by("code")})


@operator_view
@require_POST
def upload_start(request):
    """New upload or resume: same file name and size continue where the server stopped."""
    import datetime as dt
    name = request.POST.get("name", "")
    try:
        size = int(request.POST.get("size", ""))
        last_modified = dt.datetime.fromtimestamp(int(request.POST["last_modified"]) / 1000, tz=dt.timezone.utc)
        rig = upload.rig_for(name, request.POST.get("rig"))
        video, offset = upload.start(name, size, last_modified, rig)
    except (KeyError, ValueError):
        return JsonResponse({"error": "Incomplete request."}, status=400)
    except upload.UploadError as e:
        return JsonResponse({"error": str(e)}, status=409)
    return upload_json(video, offset, rig=str(video.rig))


@operator_view
@require_http_methods(["PUT"])
def upload_chunk(request, pk):
    try:
        offset = int(request.headers["X-Offset"])
        length = int(request.META.get("CONTENT_LENGTH") or 0)
        have, done = upload.write_chunk(pk, offset, request, length)
    except (KeyError, ValueError):
        return JsonResponse({"error": "Incomplete request."}, status=400)
    except IncomingVideo.DoesNotExist:
        return JsonResponse({"error": "This upload was cancelled."}, status=404)
    except upload.OffsetMismatch as e:
        return JsonResponse({"error": str(e), "offset": e.offset}, status=409)
    except upload.UploadError as e:
        return JsonResponse({"error": str(e)}, status=409)
    result = {"offset": have, "done": done}
    if done:
        video = IncomingVideo.objects.get(pk=pk)
        try:
            result["length"] = video.real_duration()
            result["warning"] = video.fps_check()[1]
        except (OSError, ValueError):
            pass
        result["registered"] = video.status == IncomingVideo.Status.REGISTERED
    return JsonResponse(result)


@operator_view
def upload_form(request, pk):
    """The metadata form as a fragment of the upload page; it is filled while the video is still uploading."""
    S = IncomingVideo.Status
    video = get_object_or_404(IncomingVideo, pk=pk)
    if video.recording_id:
        return render(request, "intake/_upload_saved.html", {"recording": video.recording})
    if video.status not in (S.UPLOADING, S.NEW):
        raise Http404("This video is no longer waiting for metadata.")
    camera = video.camera
    if video.camera_serial and camera is None:
        add_url = (reverse("admin:domain_camera_add") +
                   f"?serial={video.camera_serial}&model={video.camera_model}&usual_rig={video.rig_id}")
        return render(request, "intake/_upload_unknown_camera.html", {"video": video, "add_url": add_url})
    instance = Recording(rig=video.rig, camera=camera, operator=request.user, started_at=video.started_at)
    duration = video.real_duration() if video.status == S.NEW else None      # unknown until the file is complete
    last = None
    if request.method == "POST":
        form = RegisterForm(request.POST, instance=instance, camera_from_file=camera,
                            started_from_file=video.started_at, video_duration=duration)
        if form.is_valid():
            recording = save_registration(form, video)
            if recording is None:
                return JsonResponse({"error": "Someone else registered this video in the meantime."}, status=409)
            return render(request, "intake/_upload_saved.html", {"recording": recording})
    else:
        form, last = blank_form(video, instance, camera, duration)
    return render(request, "intake/_register_form.html",
                  {"form": form, "duration": duration, "protocol_durations": protocol_durations(),
                   "copied_from": last, "in_upload": True})


@operator_view
@require_POST
def upload_cancel(request, pk):
    video = get_object_or_404(IncomingVideo, pk=pk)
    try:
        upload.cancel(video)
    except upload.UploadError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f"Upload of {video.file_name} cancelled.")
    return redirect("intake:inbox")
