"""JSON API for workers. Every request carries  Authorization: Bearer <token>.

  POST /api/runs/claim              {"tools": ["crop@1.0.0"]}      -> 200 run spec | 204 nothing to do
  POST /api/runs/<id>/heartbeat                                       -> {"cancel": bool}
  POST /api/runs/<id>/complete      {"files": [...], "outputs": [...], "log": "..."}
  POST /api/runs/<id>/fail          {"error": "...", "log": "..."}
"""

import json
from functools import wraps

from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Run, Worker
from .tools import resolve_params

MAX_ATTEMPTS = 3


def worker_api(view):
    @csrf_exempt
    @require_POST
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        worker = Worker.objects.filter(token_hash=Worker.hash_token(token), enabled=True).first() if token else None
        if worker is None:
            return JsonResponse({"error": "invalid token"}, status=401)
        Worker.objects.filter(pk=worker.pk).update(last_seen_at=timezone.now())
        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "body must be JSON"}, status=400)
        return view(request, worker, body, *args, **kwargs)
    return wrapper


def own_run(worker, pk, *statuses) -> Run | None:
    return Run.objects.filter(pk=pk, worker=worker.name, status__in=statuses).first()


@worker_api
def claim(request, worker, body):
    offered = set(body.get("tools", [])) & set(worker.tools)
    if not offered:
        return HttpResponse(status=204)
    with transaction.atomic():
        candidates = (Run.objects.select_for_update(skip_locked=True).select_related("tool", "recording")
                      .filter(status=Run.Status.PENDING).order_by("created_at"))
        run = next((r for r in candidates if str(r.tool) in offered), None)
        if run is None:
            return HttpResponse(status=204)
        run.params = resolve_params(run)
        run.attempts += 1
        run.save(update_fields=["params", "attempts"])
        run.start(worker=worker.name)
    return JsonResponse({
        "run": run.pk, "tool": run.tool.code, "version": run.tool.version, "params": run.params,
        "recording": run.recording.code if run.recording else None, "staging": run.staging_rel,
        "inputs": [{"id": a.pk, "type": str(a.type), "rel_path": a.rel_path, "sha256": a.sha256,
                    "size_bytes": a.size_bytes, "meta": a.meta} for a in run.inputs.select_related("type")],
    })


@worker_api
def heartbeat(request, worker, body, pk):
    run = Run.objects.filter(pk=pk, worker=worker.name).first()
    if run is None:
        return JsonResponse({"error": "not your run"}, status=404)
    if run.status == Run.Status.RUNNING:
        Run.objects.filter(pk=pk).update(heartbeat_at=timezone.now())
    return JsonResponse({"cancel": run.status != Run.Status.RUNNING})


@worker_api
def complete(request, worker, body, pk):
    run = own_run(worker, pk, Run.Status.RUNNING)
    if run is None:
        return JsonResponse({"error": "not your running run"}, status=404)
    if not isinstance(body.get("files"), list) or not isinstance(body.get("outputs"), list):
        return JsonResponse({"error": "files and outputs must be lists"}, status=400)
    run.result = {"files": body["files"], "outputs": body["outputs"]}
    run.log, run.status = body.get("log", "")[-20000:], Run.Status.COMMITTING
    run.save(update_fields=["result", "log", "status"])
    return JsonResponse({"status": run.status})


@worker_api
def fail(request, worker, body, pk):
    run = own_run(worker, pk, Run.Status.RUNNING)
    if run is None:
        return JsonResponse({"error": "not your running run"}, status=404)
    error, log = body.get("error", "unknown error")[:5000], body.get("log", "")[-20000:]
    if run.attempts < MAX_ATTEMPTS:          # try again, possibly on another worker
        run.status, run.worker, run.error, run.log = Run.Status.PENDING, "", error, log
        run.save(update_fields=["status", "worker", "error", "log"])
    else:
        run.fail(error, log=log)
    return JsonResponse({"status": run.status})
