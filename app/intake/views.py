import random
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from domain.models import Arena, FlyGroup, Recording

from .forms import ArenaFormSet, FlyGroupFormSet, RegisterForm
from .models import IncomingVideo
from .preview import frame_jpeg, video_path
from .scanner import scan


def preview_context(video: IncomingVideo | None) -> dict:
    """Duration and a starting time (middle of the video) for the frame preview."""
    if video is None:
        return {}
    try:
        duration = video.ensure_duration()
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
    done = (IncomingVideo.objects.filter(status__in=[IncomingVideo.Status.REGISTERED, IncomingVideo.Status.INGESTED])
            .select_related("rig", "recording")[:20])
    return render(request, "intake/inbox.html", {"pending": pending, "done": done})


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
    if camera and camera.usual_rig_id and camera.usual_rig_id != video.rig_id:
        warnings.append(f"Camera {camera} usually sits on {camera.usual_rig}, but this video came from "
                        f"{video.rig}. Were the cameras swapped?")

    instance = Recording(rig=video.rig, camera=camera, operator=request.user, started_at=video.started_at)
    if request.method == "POST":
        form = RegisterForm(request.POST, instance=instance, camera_from_file=camera,
                            started_from_file=video.started_at)
        if form.is_valid():
            with transaction.atomic():
                locked = IncomingVideo.objects.select_for_update().get(pk=video.pk)
                if locked.status != IncomingVideo.Status.NEW:
                    messages.error(request, "Someone else registered this video in the meantime.")
                    return redirect("intake:inbox")
                recording = form.save(commit=False)
                if video.started_at:                 # authoritative, keeps milliseconds
                    recording.started_at = video.started_at
                recording.save()
                recording.populate_arenas()
                locked.recording, locked.status = recording, IncomingVideo.Status.REGISTERED
                locked.save(update_fields=["recording", "status"])
            messages.success(request, f"Created {recording.code}. Check the arenas below.")
            return redirect("intake:arenas", code=recording.code)
    else:
        last = Recording.objects.filter(rig=video.rig).order_by("-started_at").first()
        initial = {f: getattr(last, f) for f in COPY_FROM_LAST} if last else {}
        form = RegisterForm(instance=instance, initial=initial, camera_from_file=camera,
                            started_from_file=video.started_at)
        if last:
            messages.info(request, f"Fields copied from the last recording on {video.rig} ({last.code}).")
    return render(request, "intake/register.html",
                  {"video": video, "form": form, "warnings": warnings, **preview_context(video)})


@operator_view
def arenas(request, code):
    recording = get_object_or_404(Recording, code=code)
    arena_qs = Arena.objects.filter(recording=recording).order_by("index")
    group_qs = FlyGroup.objects.filter(arena__recording=recording).select_related("arena").order_by("arena__index", "zone")
    if request.method == "POST":
        arena_fs = ArenaFormSet(request.POST, queryset=arena_qs, prefix="arena")
        group_fs = FlyGroupFormSet(request.POST, queryset=group_qs, prefix="group")
        if arena_fs.is_valid() and group_fs.is_valid():
            with transaction.atomic():
                group_fs.save()      # groups first: an arena marked empty then removes its groups
                arena_fs.save()
            messages.success(request, f"{recording.code} saved.")
            return redirect("intake:inbox")
    else:
        arena_fs = ArenaFormSet(queryset=arena_qs, prefix="arena")
        group_fs = FlyGroupFormSet(queryset=group_qs, prefix="group")

    layout = recording.layout
    grid = [[None] * layout.cols for _ in range(layout.rows)]
    for form in arena_fs.forms:
        r, c = layout.position(form.instance.index)
        grid[r - 1][c - 1] = form
    video = getattr(recording, "incoming_video", None)
    return render(request, "intake/arenas.html",
                  {"recording": recording, "arena_fs": arena_fs, "group_fs": group_fs, "grid": grid,
                   **preview_context(video)})


@operator_view
def frame(request, pk):
    """JPEG of one frame; ?t=seconds, or a random time if t is missing."""
    video = get_object_or_404(IncomingVideo, pk=pk)
    duration = video.ensure_duration()
    if not duration:
        raise Http404("Cannot read this video.")
    try:
        t = float(request.GET["t"])
    except (KeyError, ValueError):
        t = random.uniform(0.05, 0.95) * duration
    t = round(min(max(t, 0.0), max(duration - 0.5, 0.0)), 1)
    cache = Path(settings.MEDIA_ROOT) / "previews" / str(video.pk)
    path = frame_jpeg(video_path(video.rel_path), t, cache)
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
