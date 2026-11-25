"""The pages people use every day: recordings, one recording, review, experiments, machines, settings."""

import datetime as dt
import shutil
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import Count, Min, Prefetch, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Asset, Review, Run, TrackerTemplate, Worker
from core.quality import ensure_quality
from core.storage import resolve
from domain.models import Arena, Camera, Condition, Experiment, Genotype, Recording
from intake.models import IncomingVideo

from . import status as S

operator = [login_required, permission_required("domain.add_recording", raise_exception=True)]


def operator_view(view):
    for deco in reversed(operator):
        view = deco(view)
    return view


def recordings_queryset():
    return (Recording.objects.select_related("experiment", "condition", "rig", "layout", "incoming_video", "operator")
            .prefetch_related(Prefetch("runs", queryset=Run.objects.select_related("tool", "arena")),
                              Prefetch("assets", queryset=Asset.objects.select_related("type", "review")),
                              "arenas"))


def tracking_ready() -> set:
    return set(TrackerTemplate.objects.filter(active=True).values_list("layout_id", flat=True))


def thumb_cells(rec, st):
    return [{"code": a.code, "state": a.state} for a in st.arenas] or [{"code": "", "state": "waiting"}] * rec.layout.n_arenas


# --- Recordings ---------------------------------------------------------------------------------

@operator_view
def recordings(request):
    show = request.GET.get("show", "all")
    q = request.GET.get("q", "").strip()
    mine = request.GET.get("mine", "0" if request.user.is_staff else "1") == "1"
    recs = recordings_queryset()
    if mine:
        recs = recs.filter(operator=request.user)
    if q:
        recs = recs.filter(Q(code__icontains=q) | Q(experiment__code__icontains=q) | Q(experiment__name__icontains=q)
                           | Q(condition__code__icontains=q))
    ready, now = tracking_ready(), timezone.now()
    rows = []
    for rec in recs:
        st = S.recording_status(rec, ready, now)
        label, url, primary = S.action_for(st, rec=rec)
        rows.append({"rec": rec, "st": st, "title": f"{rec.experiment.name} · {rec.condition}",
                     "meta": f"{rec.rig} · {timezone.localtime(rec.started_at):%d.%m.%Y %H:%M} · {rec.layout}",
                     "cells": thumb_cells(rec, st), "cols": rec.layout.cols, "action": label, "url": url,
                     "primary": primary, "frame": frame_url(rec)})
    if not q:
        loose = IncomingVideo.objects.filter(recording__isnull=True, status__in=[
            IncomingVideo.Status.UPLOADING, IncomingVideo.Status.NEW]).select_related("rig")
        for v in loose:
            st = S.incoming_status(v, now)
            label, url, primary = S.action_for(st, video=v)
            rows.append({"rec": None, "video": v, "st": st, "title": "Details not entered yet",
                         "meta": f"{v.rig} · {timezone.localtime(v.started_at):%d.%m.%Y %H:%M}" if v.started_at else str(v.rig),
                         "cells": [], "cols": 1, "action": label, "url": url, "primary": primary, "frame": None})
    counts = {g: sum(r["st"].group == g for r in rows) for g, _ in S.GROUPS}
    groups = []
    for key, title in S.GROUPS:
        if show not in ("all", key):
            continue
        items = [r for r in rows if r["st"].group == key]
        more = 0
        if key == S.FINISHED and show == "all" and len(items) > 10:
            more, items = len(items) - 10, items[:10]
        if items or show == key:
            groups.append({"key": key, "title": title, "rows": items, "count": counts[key], "more": more})
    return render(request, "web/recordings.html", {
        "groups": groups, "counts": counts, "total": len(rows), "show": show, "q": q, "mine": mine,
        "filters": [("all", "All", len(rows))] + [(k, t, counts[k]) for k, t in S.GROUPS],
        "n_experiments": Experiment.objects.count()})


def frame_url(rec):
    try:
        video = rec.incoming_video
    except IncomingVideo.DoesNotExist:
        return None
    if video.status == IncomingVideo.Status.UPLOADING or not video.duration_s:
        return None
    return reverse("intake:frame", args=[video.pk]) + f"?t={video.duration_s / 2:.1f}&size=thumb"


# --- One recording -----------------------------------------------------------------------------

RUN_WORDS = {"ingest": "Copy and check", "crop": "Cut out arenas", "flytracker": "Tracking (FlyTracker)",
             "upload": "Upload"}


@operator_view
def recording(request, code):
    rec = get_object_or_404(recordings_queryset(), code=code)
    st = S.recording_status(rec, tracking_ready())
    for a in st.arenas:
        if a.trajectories is not None and a.quality is None:
            a.quality = ensure_quality(a.trajectories)
    raw = next((a for a in rec.assets.all() if a.type.code == "raw_video"), None)
    notices = []
    if raw and raw.meta.get("fps_used") and raw.meta.get("fps") and abs(raw.meta["fps_used"] - raw.meta["fps"]) > 0.01:
        notices.append({"title": "Camera speed corrected", "text":
                        f"The camera recorded at {raw.meta['fps_used']:.2f} frames per second, the file says "
                        f"{raw.meta['fps']:g}. All times here use the real speed, so nothing to do for you.",
                        "admin": "Limit the camera's frame rate in pylon (Acquisition Frame Rate Enable)."})
    if st.attention:
        notices.append({"title": "Needs attention", "text": st.attention, "admin": ""})
    failed = [r for r in rec.runs.all() if r.status == Run.Status.FAILED]
    try:
        video = rec.incoming_video
    except IncomingVideo.DoesNotExist:
        video = None
    duration = video.real_duration() if video and video.duration_s else None
    history = sorted(rec.runs.all(), key=lambda r: r.created_at, reverse=True)
    for r in history:
        r.word = RUN_WORDS.get(r.tool.code, str(r.tool))
        if r.arena_id:
            r.word += f" · A{r.arena.index:02d}" if r.arena else ""
    return render(request, "web/recording.html", {
        "rec": rec, "st": st, "raw": raw, "notices": notices, "failed": failed, "video": video,
        "frame_t": round(duration / 2, 1) if duration else None, "history": history[:40],
        "duration": duration, "real_minutes": raw.meta.get("recorded_s") if raw else None})


# --- Review ------------------------------------------------------------------------------------

def review_queue():
    return (Asset.objects.filter(type__code="trajectories", status=Asset.Status.OK, review__isnull=True,
                                 arena__status=Arena.Status.OK)
            .exclude(recording__status=Recording.Status.REJECTED)
            .order_by("recording__started_at", "arena__index", "pk"))


@operator_view
def review_next(request):
    queue = list(review_queue().values_list("pk", flat=True))
    after = request.GET.get("after")
    if after and after.isdigit() and int(after) in queue:
        i = queue.index(int(after))
        queue = queue[i + 1:] + queue[:i]
    if not queue:
        return render(request, "web/review_empty.html", {})
    return redirect("web:review_item", pk=queue[0])


DECISIONS = {"accept": Review.Decision.ACCEPTED, "tracking_bad": Review.Decision.TRACKING_BAD,
             "unusable": Review.Decision.UNUSABLE}
UNUSABLE_REASONS = ["A fly died", "A fly escaped", "Wrong flies or number", "Arena empty", "Lighting or focus",
                    "Something else"]


@operator_view
def review_item(request, pk):
    asset = get_object_or_404(Asset.objects.select_related("recording__experiment", "recording__condition", "arena",
                                                           "produced_by"), pk=pk, type__code="trajectories")
    if request.method == "POST":
        decision = DECISIONS.get(request.POST.get("decision", ""))
        if decision is None:
            return HttpResponse("Unknown decision.", status=400)
        Review.objects.update_or_create(asset=asset, defaults={
            "decision": decision, "note": request.POST.get("note", "").strip(),
            "reason": request.POST.get("reason", "") if decision == Review.Decision.UNUSABLE else "",
            "decided_by": request.user, "decided_at": timezone.now()})
        messages.success(request, f"{asset.arena.code}: {S.DECISION_WORDS[decision]}.")
        return redirect(reverse("web:review_next") + f"?after={asset.pk}")

    q = ensure_quality(asset) or {}
    rec = asset.recording
    siblings = []
    for a in Asset.objects.filter(type__code="trajectories", recording=rec).select_related("arena", "review").order_by(
            "arena__index", "-pk"):
        if any(s["arena"] == a.arena_id for s in siblings):
            continue
        siblings.append({"arena": a.arena_id, "code": f"A{a.arena.index:02d}", "pk": a.pk, "current": a.pk == asset.pk,
                         "state": a.review.decision if S.reviewed(a) else (a.meta.get("quality") or {}).get("verdict", "check")})
    waiting = review_queue().count()
    try:
        existing = asset.review
    except Review.DoesNotExist:
        existing = None
    flies = sum(g.n_flies or 0 for g in asset.arena.fly_groups.all())
    return render(request, "web/review.html", {
        "asset": asset, "rec": rec, "q": q, "checks": checks(q, asset), "bins": flag_bins(asset),
        "siblings": siblings, "waiting": waiting, "existing": existing, "reasons": UNUSABLE_REASONS,
        "flies": flies, "minutes": q.get("minutes")})


def checks(q: dict, asset) -> list:
    if not q:
        return []
    exp = q.get("flies_expected")
    out = [{"name": "Flies found", "value": f"{q['flies_found']} of {exp}" if exp else str(q["flies_found"]),
            "ok": not exp or q["flies_found"] == exp, "note": "As entered for this arena." if exp else ""}]
    ratio = q.get("scale_ratio")
    ppm = (asset.meta.get("calibration") or {}).get("ppm")
    out.append({"name": "Arena and scale", "value": f"{ppm:.1f} px/mm" if ppm else "—",
                "ok": ratio is None or abs(ratio - 1) <= 0.05,
                "note": "Matches the layout's arena size." if ratio and abs(ratio - 1) <= 0.05 else
                        "Differs from the layout's arena size." if ratio else ""})
    out.append({"name": "Lost positions", "value": f"{100 * q['lost_fraction']:.1f} %",
                "ok": q["lost_fraction"] <= 0.02, "note": "How often a fly could not be found."})
    fpm = q.get("flags_per_min")
    out.append({"name": "Possible identity swaps", "value": f"{fpm:.0f} per min" if fpm is not None else "—",
                "ok": fpm is None or fpm <= 10, "note": "Moments where the tracker was unsure which fly is which."})
    out.append({"name": "Impossible jumps", "value": str(q["jumps"]), "ok": q["jumps_per_fly_min"] <= 0.5,
                "note": "Flies moving faster than 50 mm/s, usually at a swap."})
    return out


def flag_bins(asset, n=60) -> list:
    flags = asset.meta.get("flags") or []
    lo, hi = (asset.meta.get("frame_range") or [0, asset.meta.get("n_frames") or 1])
    span = max(hi - lo, 1)
    counts = [0] * n
    for f in flags:
        frame = (f.get("frames") or [lo])[0]
        counts[min(n - 1, max(0, int((frame - lo) / span * n)))] += 1
    top = max(counts) or 1
    return [{"h": round(100 * c / top), "n": c} for c in counts]


@operator_view
def paths_svg(request, pk):
    """Paths of every fly over the arena: a quick look at whether the tracker followed them."""
    import numpy as np
    import pandas as pd
    asset = get_object_or_404(Asset, pk=pk, type__code="trajectories")
    try:
        df = pd.read_parquet(resolve(asset.rel_path), columns=["frame", "fly", "x_px", "y_px"])
    except Exception:  # noqa: BLE001
        raise Http404("Trajectories not readable.")
    crop = crop_of(asset)
    w = (crop.meta.get("width") if crop else None) or float(df["x_px"].max() or 1)
    h = (crop.meta.get("height") if crop else None) or float(df["y_px"].max() or 1)
    step = max(1, int(df["frame"].nunique() / 900))
    colours = ["#3f7fe0", "#d4473a", "#8d6bd1", "#e0a030", "#2f9e6e", "#1d4f9c", "#c0398a", "#5b8f2a", "#7a4508",
               "#1b8a8f", "#5c6461", "#9c2a20"]
    lines = []
    for i, (fly, d) in enumerate(df.sort_values(["fly", "frame"]).groupby("fly")):
        d = d.iloc[::step].dropna()
        pts = " ".join(f"{x:.0f},{y:.0f}" for x, y in zip(d["x_px"], d["y_px"]))
        lines.append(f'<polyline points="{pts}" fill="none" stroke="{colours[i % len(colours)]}" '
                     f'stroke-width="{max(w, h) / 400:.1f}" stroke-linejoin="round" opacity=".85"/>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" preserveAspectRatio="xMidYMid meet">'
           + "".join(lines) + "</svg>")
    return HttpResponse(svg, content_type="image/svg+xml")


def crop_of(asset):
    return asset.produced_by.inputs.filter(type__code="crop_video").first()


@operator_view
def arena_frame(request, pk):
    """A frame from the arena's crop, the background for its paths."""
    from intake.preview import frame_jpeg
    asset = get_object_or_404(Asset, pk=pk, type__code="trajectories")
    crop = crop_of(asset)
    if crop is None:
        raise Http404("No crop for this result.")
    n, fps = crop.meta.get("n_frames") or 0, crop.meta.get("fps") or 1
    path = frame_jpeg(resolve(crop.rel_path), round(n / fps / 2, 1), Path(settings.MEDIA_ROOT) / "previews" /
                      f"crop-{crop.pk}", width=1000)
    response = FileResponse(open(path, "rb"), content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=86400"
    return response


# --- Experiments -------------------------------------------------------------------------------

@operator_view
def experiments(request):
    exps = Experiment.objects.prefetch_related(
        Prefetch("recordings", queryset=Recording.objects.select_related("condition__genotype").prefetch_related(
            Prefetch("assets", queryset=Asset.objects.filter(type__code="trajectories").select_related("review")))))
    out = []
    for e in exps:
        rows = defaultdict(lambda: {"recorded": 0, "usable": 0, "accepted": 0, "tracked": 0, "waiting": 0, "kept": 0})
        for rec in e.recordings.all():
            r = rows[rec.condition]
            r["recorded"] += 1
            traj = list(rec.assets.all())
            decisions = [a.review.decision for a in traj if S.reviewed(a)]
            r["tracked"] += len(traj)
            r["accepted"] += decisions.count(Review.Decision.ACCEPTED)
            r["kept"] += decisions.count(Review.Decision.TRACKING_BAD)
            r["usable"] += Review.Decision.ACCEPTED in decisions
            if rec.status != Recording.Status.REJECTED and (not traj or len(decisions) < len(traj)):
                r["waiting"] += 1
        conds = []
        for cond, r in sorted(rows.items(), key=lambda kv: kv[0].code):
            r["pct"] = min(100, round(100 * r["usable"] / e.target_recordings)) if e.target_recordings else 0
            conds.append({"cond": cond, **r})
        out.append({"e": e, "conds": conds})
    return render(request, "web/experiments.html", {"experiments": out})


@operator_view
@require_POST
def experiment_new(request):
    code, name = request.POST.get("code", "").strip(), request.POST.get("name", "").strip()
    try:
        target = int(request.POST.get("target") or 20)
        e = Experiment(code=code, name=name or code, target_recordings=target, owner=request.user,
                       start_date=timezone.localdate())
        e.full_clean()
        e.save()
    except Exception as ex:  # noqa: BLE001 - shown to the person
        msg = "; ".join(sum(ex.message_dict.values(), [])) if hasattr(ex, "message_dict") else str(ex)
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"error": msg}, status=400)
        messages.error(request, msg)
        return redirect("web:experiments")
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"id": e.pk, "label": str(e)})
    messages.success(request, f"Experiment {e.code} created.")
    return redirect("web:experiments")


@operator_view
@require_POST
def condition_new(request):
    """Inline from the upload form: a new condition, and its genotype if that is new too."""
    code = request.POST.get("code", "").strip()
    try:
        with transaction.atomic():
            g_new = request.POST.get("genotype_new", "").strip()
            genotype = (Genotype.objects.get_or_create(code=g_new, defaults={"name": g_new})[0] if g_new
                        else Genotype.objects.get(pk=request.POST.get("genotype")))
            c = Condition(code=code, genotype=genotype, description=request.POST.get("description", "").strip())
            c.full_clean()
            c.save()
    except Genotype.DoesNotExist:
        return JsonResponse({"error": "Choose a genotype."}, status=400)
    except Exception as ex:  # noqa: BLE001
        msg = "; ".join(sum(ex.message_dict.values(), [])) if hasattr(ex, "message_dict") else str(ex)
        return JsonResponse({"error": msg}, status=400)
    return JsonResponse({"id": c.pk, "label": str(c)})


# --- Machines ----------------------------------------------------------------------------------

def machine_state(w, running, now):
    if not w.enabled:
        return "Disabled", "idle", "Switched off in the admin.", ""
    if running:
        r = running[0]
        what = RUN_WORDS.get(r.tool.code, str(r.tool))
        target = f"{r.recording.code}" + (f" · arena A{r.arena.index:02d}" if r.arena else "") if r.recording else ""
        since = f"started {timezone.localtime(r.started_at):%H:%M}" if r.started_at else ""
        return "Working", "run", f"{what} · {target}", since
    if w.state == "paused" and w.state_at and now - w.state_at < dt.timedelta(minutes=10):
        return "Paused", "check", "Not working right now", w.state_detail
    if w.last_seen_at and now - w.last_seen_at < dt.timedelta(minutes=3):
        return "Idle", "ok", "Waiting for work", ""
    seen = f"last seen {timezone.localtime(w.last_seen_at):%d.%m. %H:%M}" if w.last_seen_at else "never connected"
    return "Offline", "idle", "Not connected", seen


@operator_view
def machines(request):
    now = timezone.now()
    runs = Run.objects.select_related("tool", "recording", "arena")
    workers = []
    for w in Worker.objects.order_by("name"):
        running = list(runs.filter(worker=w.name, status__in=[Run.Status.RUNNING, Run.Status.COMMITTING]))
        state, kind, doing, detail = machine_state(w, running, now)
        week = (Run.objects.filter(worker=w.name, status=Run.Status.DONE, finished_at__gte=now - dt.timedelta(days=7))
                .values("tool__code").annotate(n=Count("id")))
        workers.append({"w": w, "state": state, "kind": kind, "doing": doing, "detail": detail,
                        "tools": ", ".join(RUN_WORDS.get(t.split("@")[0], t) for t in w.tools) or "—",
                        "week": " · ".join(f"{x['n']} × {RUN_WORDS.get(x['tool__code'], x['tool__code'])}" for x in week)
                        or "nothing"})
    later = set(runs.exclude(status__in=[Run.Status.FAILED, Run.Status.CANCELLED])
                .values_list("tool_id", "recording_id", "arena_id", "pk"))
    problems = []
    for r in runs.filter(status=Run.Status.FAILED).exclude(tool__code="ingest").order_by("-finished_at")[:50]:
        if any(t == r.tool_id and rec == r.recording_id and ar == r.arena_id and pk > r.pk for t, rec, ar, pk in later):
            continue                                           # already done again or queued again
        r.word, r.plain = RUN_WORDS.get(r.tool.code, str(r.tool)), S.plain_error(r.error)
        problems.append(r)
    queue = (runs.filter(status=Run.Status.PENDING).values("tool__code", "recording__code")
             .annotate(n=Count("id"), since=Min("created_at")).order_by("since"))
    for q in queue:
        q["word"] = RUN_WORDS.get(q["tool__code"], q["tool__code"])
    return render(request, "web/machines.html", {"workers": workers, "problems": problems, "queue": list(queue),
                                                 "n_pending": runs.filter(status=Run.Status.PENDING).count()})


@login_required
@permission_required("core.change_run", raise_exception=True)
@require_POST
def run_retry(request, pk):
    run = get_object_or_404(Run, pk=pk, status=Run.Status.FAILED)
    run.status, run.attempts, run.worker = Run.Status.PENDING, 0, ""
    run.log = f"{run.log}\n--- retried by {request.user} at {timezone.now():%Y-%m-%d %H:%M} after: {run.error}".strip()
    run.error = ""
    run.save(update_fields=["status", "attempts", "worker", "log", "error"])
    messages.success(request, "It will be tried again by the next free machine.")
    return redirect("web:machines")


# --- Settings ----------------------------------------------------------------------------------

@operator_view
def settings_page(request):
    from core.flytracker import calibration_info, template_warnings
    from domain.models import ArenaLayout
    layouts = []
    templates = {t.layout_id: t for t in TrackerTemplate.objects.filter(active=True).select_related("asset")}
    for l in ArenaLayout.objects.order_by("code"):
        t = templates.get(l.pk)
        waiting = (Asset.objects.filter(type__code="crop_video", recording__layout=l, consumed_by__isnull=True)
                   .values("recording").distinct().count())
        row = {"l": l, "t": t, "waiting": waiting, "state": "Not set up", "kind": "idle", "note": "", "problems": []}
        if t:
            try:
                info = calibration_info(resolve(t.asset.rel_path))
                row["problems"] = template_warnings(info, t.mode, l.arena_diameter_mm)
            except Exception as e:  # noqa: BLE001
                row["problems"] = [f"The calibration file cannot be read: {e}"]
            m = t.asset.meta
            row["note"] = f"{t.name} · {m.get('ppm', 0):.2f} px/mm · {t.get_mode_display().split(':')[0]}"
            row["state"], row["kind"] = ("Needs a new calibration", "bad") if row["problems"] else ("Ready", "ok")
        layouts.append(row)
    cameras = []
    for c in Camera.objects.select_related("usual_rig").order_by("code"):
        raw = (Asset.objects.filter(type__code="raw_video", recording__camera=c)
               .order_by("-recording__started_at").first())
        if raw is None:
            check, kind = "No recordings yet.", "idle"
        elif raw.meta.get("fps_used") and abs(raw.meta["fps_used"] - raw.meta.get("fps", 0)) > 0.01:
            check, kind = (f"Records at {raw.meta['fps_used']:.2f} fps while its files say {raw.meta['fps']:g}. "
                           "Corrected automatically; fix in pylon: Acquisition Frame Rate Enable on, "
                           f"{raw.meta['fps']:g} fps."), "check"
        else:
            check, kind = "Frame rate OK in the last recording.", "ok"
        cameras.append({"c": c, "check": check, "kind": kind})
    per_condition = dict(Recording.objects.values_list("condition").annotate(n=Count("id")))
    conditions = [{"c": c, "n": per_condition.get(c.pk, 0)}
                  for c in Condition.objects.select_related("genotype").order_by("code")]
    people = get_user_model().objects.filter(is_active=True).order_by("-is_superuser", "username")
    sizes = dict(Asset.objects.values_list("type__code").annotate(s=Sum("size_bytes")))
    try:
        disk = shutil.disk_usage(settings.VIDEOS_ROOT)
    except OSError:
        disk = None
    raw_b, crop_b = sizes.pop("raw_video", 0) or 0, sizes.pop("crop_video", 0) or 0
    other_b = sum(v or 0 for v in sizes.values())
    storage = {"raw": raw_b, "crop": crop_b, "other": other_b, "disk": disk}
    if disk:
        storage.update({f"{k}_pct": round(100 * v / disk.total, 1) for k, v in
                        (("raw", raw_b), ("crop", crop_b), ("other", other_b))})
        per_rec = raw_b / max(1, Asset.objects.filter(type__code="raw_video").count())
        storage["more"] = int(disk.free / per_rec) if raw_b else None
    return render(request, "web/settings.html", {"layouts": layouts, "cameras": cameras, "conditions": conditions,
                                                 "people": people, "storage": storage})

