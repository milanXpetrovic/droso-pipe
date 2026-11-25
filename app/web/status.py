"""Where each recording is, in the words the pages use.

Five steps: Upload, Check (ingest), Arenas (crop), Tracking, Review. Each step has a state and a short text;
the recording falls into one group: needs attention, in progress, ready for review, or done.
Everything is derived from what is stored (runs, assets, reviews), never kept separately.
"""

import datetime as dt
from dataclasses import dataclass, field

from django.urls import reverse
from django.utils import timezone

from core.api import MAX_ATTEMPTS
from core.models import Review, Run
from domain.models import Arena
from intake.models import IncomingVideo

DONE, RUNNING, WAITING, FAILED, NEEDS_YOU, NOT_STARTED = "done", "running", "waiting", "failed", "needs_you", "not_started"
STEP_NAMES = ["Upload", "Check", "Arenas", "Tracking", "Review"]
ATTENTION, PROGRESS, REVIEW, FINISHED = "attention", "progress", "review", "done"
GROUPS = [(ATTENTION, "Needs attention"), (PROGRESS, "In progress"), (REVIEW, "Ready for review"), (FINISHED, "Done")]
STALLED_UPLOAD = dt.timedelta(minutes=10)
NOT_TRACKERS = {"ingest", "crop", "upload"}
DECISION_WORDS = {Review.Decision.ACCEPTED: "accepted", Review.Decision.TRACKING_BAD: "kept for a better tracker",
                  Review.Decision.UNUSABLE: "can't be used"}


@dataclass
class Step:
    name: str
    state: str = NOT_STARTED
    text: str = ""


@dataclass
class ArenaState:
    index: int
    code: str
    state: str                # excluded / accepted / kept / unusable / good / check / bad / tracking / failed / waiting
    label: str
    trajectories: object = None
    review: object = None
    quality: dict | None = None
    worker: str = ""


@dataclass
class Status:
    steps: list
    group: str
    sentence: str
    attention: str = ""
    arenas: list = field(default_factory=list)
    quality_label: str = "—"
    quality_kind: str = "idle"
    to_review: list = field(default_factory=list)

    @property
    def steps_label(self):
        return ", ".join(f"{s.name} {s.state.replace('_', ' ')}" for s in self.steps)


def latest(items):
    return max(items, key=lambda x: x.pk) if items else None


def upload_step(video: IncomingVideo | None, now) -> tuple[Step, str]:
    """Step and an attention text (a stalled upload)."""
    if video is None or video.status != IncomingVideo.Status.UPLOADING:
        size = f"{video.size_bytes / 1e9:.1f} GB" if video else ""
        return Step("Upload", DONE, size), ""
    from intake.upload import part_path, received
    have, total = received(video), video.size_bytes or 1
    pct = int(100 * have / total)
    part = part_path(video)
    last = dt.datetime.fromtimestamp(part.stat().st_mtime, tz=dt.timezone.utc) if part.exists() else video.first_seen_at
    if now - last > STALLED_UPLOAD:
        return (Step("Upload", NEEDS_YOU, f"Stopped at {pct} %"),
                f"Upload stopped at {pct} %. Drop the same file on the Upload page to continue.")
    return Step("Upload", RUNNING, f"{pct} % · {have / 1e9:.1f} of {total / 1e9:.1f} GB"), ""


def recording_status(rec, tracking_ready: set, now=None) -> Status:
    """rec with prefetched runs (+tool), assets (+type, review), arenas; tracking_ready: layout ids with an active
    tracker template."""
    now = now or timezone.now()
    try:
        video = rec.incoming_video
    except IncomingVideo.DoesNotExist:
        video = None
    runs = [r for r in rec.runs.all() if r.status != Run.Status.CANCELLED]
    assets = list(rec.assets.all())
    raw = next((a for a in assets if a.type.code == "raw_video"), None)
    arenas = sorted(rec.arenas.all(), key=lambda a: a.index)
    valid = [a for a in arenas if a.status == Arena.Status.OK]
    attention = ""

    upload, attention = upload_step(video, now)

    check = Step("Check")
    if raw:
        frames = raw.meta.get("n_frames")
        check.state, check.text = DONE, f"Copy verified · {frames:,} frames".replace(",", " ") if frames else "Copy verified"
    elif video and video.status == IncomingVideo.Status.INGESTING:
        check.state, check.text = RUNNING, "Copying and checking"
    elif video and video.status == IncomingVideo.Status.REGISTERED:
        if video.ingest_attempts >= MAX_ATTEMPTS:
            check.state, check.text = FAILED, video.ingest_error or "Copy check failed"
        else:
            check.state, check.text = WAITING, "Waiting for the server"

    crop_step = Step("Arenas")
    crop = latest([r for r in runs if r.tool.code == "crop"])
    if raw and crop is None:
        crop_step.state, crop_step.text = WAITING, "Waiting for a machine"
    elif crop:
        crop_step.state, crop_step.text = run_state(crop, f"{len(valid)} arenas")

    track = Step("Tracking")
    trackers = {}
    for r in runs:
        if r.tool.code not in NOT_TRACKERS and r.arena_id:
            if r.arena_id not in trackers or r.pk > trackers[r.arena_id].pk:
                trackers[r.arena_id] = r
    if crop_step.state == DONE:
        n = len(valid)
        states = [trackers[a.pk].status for a in valid if a.pk in trackers]
        n_done = states.count(Run.Status.DONE)
        n_failed = states.count(Run.Status.FAILED)
        running = [trackers[a.pk] for a in valid if a.pk in trackers
                   and trackers[a.pk].status in (Run.Status.RUNNING, Run.Status.COMMITTING)]
        if not trackers and rec.layout_id not in tracking_ready:
            track.state, track.text = WAITING, f"Tracking is not set up for layout {rec.layout}"
            attention = attention or f"Tracking is not set up for layout {rec.layout} yet (admin: Settings)."
        elif n_failed:
            track.state, track.text = FAILED, f"{n_failed} of {n} arenas failed"
        elif running:
            track.state = RUNNING
            track.text = f"{n_done} of {n} arenas done · on {running[0].worker or 'a machine'}"
        elif n_done >= n and n:
            track.state, track.text = DONE, f"{n} arenas"
        else:
            track.state, track.text = WAITING, f"{n_done} of {n} arenas done · waiting for a machine"

    traj = {}
    for a in assets:
        if a.type.code == "trajectories" and a.arena_id:
            if a.arena_id not in traj or a.pk > traj[a.arena_id].pk:
                traj[a.arena_id] = a
    review = Step("Review")
    to_review = [traj[a.pk] for a in valid if a.pk in traj and not reviewed(traj[a.pk])]
    decided = [traj[a.pk] for a in valid if a.pk in traj and reviewed(traj[a.pk])]
    if to_review:
        review.state, review.text = NEEDS_YOU, f"{len(to_review)} arena{'s' if len(to_review) != 1 else ''} waiting for review"
    elif decided and track.state == DONE:
        review.state, review.text = DONE, decisions_text(decided)

    arena_states = [arena_state(a, traj.get(a.pk), trackers.get(a.pk)) for a in arenas]
    steps = [upload, check, crop_step, track, review]
    failed = next((s for s in steps if s.state == FAILED), None)
    if failed or attention:
        group = ATTENTION
    elif all(s.state == DONE for s in steps):
        group = FINISHED
    elif review.state == NEEDS_YOU and track.state in (DONE, FAILED):
        group = REVIEW
    else:
        group = PROGRESS
    sentence = (f"{failed.name}: {failed.text}" if failed else attention
                or next((f"{s.name}: {s.text}" for s in steps if s.state in (RUNNING, WAITING)), "")
                or (review.text if review.state == NEEDS_YOU else "")
                or (f"Reviewed · {review.text}" if review.state == DONE else "Waiting"))
    q_label, q_kind = quality_summary(arena_states)
    return Status(steps, group, sentence, attention, arena_states, q_label, q_kind, to_review)


def reviewed(asset) -> bool:
    try:
        return asset.review is not None
    except Review.DoesNotExist:
        return False


def run_state(run, done_text: str) -> tuple[str, str]:
    if run.status == Run.Status.DONE:
        return DONE, done_text
    if run.status in (Run.Status.RUNNING, Run.Status.COMMITTING):
        return RUNNING, f"On {run.worker or 'a machine'}"
    if run.status == Run.Status.FAILED:
        return FAILED, plain_error(run.error)
    return WAITING, "Waiting for a machine" + (" (trying again after an error)" if run.attempts else "")


def plain_error(error: str) -> str:
    """The first line of an error, without Python's exception class names."""
    line = (error or "Failed").strip().splitlines()[0]
    for prefix in ("RuntimeError: ", "PermissionError: ", "CalledProcessError: ", "Commit failed: "):
        line = line.replace(prefix, "")
    return line[:160]


def decisions_text(assets) -> str:
    counts = {}
    for a in assets:
        counts[a.review.decision] = counts.get(a.review.decision, 0) + 1
    return " · ".join(f"{n} {DECISION_WORDS[d]}" for d, n in counts.items())


def arena_state(arena, traj, tracker) -> ArenaState:
    code = f"A{arena.index:02d}"
    if arena.status != Arena.Status.OK:
        return ArenaState(arena.index, code, "excluded", "left out")
    if traj is not None:
        q = traj.meta.get("quality")
        if reviewed(traj):
            d = traj.review.decision
            kind = {"accepted": "accepted", "tracking_bad": "kept", "unusable": "unusable"}[d]
            return ArenaState(arena.index, code, kind, DECISION_WORDS[d], traj, traj.review, q)
        verdict = (q or {}).get("verdict", "check")
        return ArenaState(arena.index, code, verdict, {"good": "looks good", "check": "check", "bad": "looks bad"}[verdict],
                          traj, None, q)
    if tracker is not None:
        if tracker.status == Run.Status.FAILED:
            return ArenaState(arena.index, code, "failed", "tracking failed", worker=tracker.worker)
        if tracker.status in (Run.Status.RUNNING, Run.Status.COMMITTING):
            return ArenaState(arena.index, code, "tracking", "tracking now", worker=tracker.worker)
    return ArenaState(arena.index, code, "waiting", "waiting")


def quality_summary(arenas) -> tuple[str, str]:
    tracked = [a for a in arenas if a.trajectories is not None]
    failed = [a for a in arenas if a.state == "failed"]
    if failed:
        return f"{len(failed)} failed", "bad"
    if not tracked:
        return "—", "idle"
    if all(a.review is not None for a in tracked):
        acc = sum(a.state == "accepted" for a in tracked)
        return f"{acc} accepted", "ok" if acc else "idle"
    counts = {k: sum(a.state == k for a in tracked) for k in ("good", "check", "bad")}
    parts = [f"{n} {k}" for k, n in counts.items() if n]
    kind = "bad" if counts["bad"] else "check" if counts["check"] else "ok"
    return " · ".join(parts) or "—", kind


def incoming_status(video: IncomingVideo, now=None) -> Status:
    """A video that is not a recording yet: uploading without saved details, or dropped into incoming/."""
    now = now or timezone.now()
    upload, attention = upload_step(video, now)
    steps = [upload] + [Step(n) for n in STEP_NAMES[1:]]
    if video.status == IncomingVideo.Status.NEW:
        attention = "Waiting for its details."
    group = ATTENTION if attention else PROGRESS
    return Status(steps, group, attention or f"Upload: {upload.text}", attention)


def action_for(status: Status, rec=None, video=None) -> tuple[str, str, bool]:
    """(label, url, primary) of the one button on a row."""
    if video is not None and rec is None:
        if video.status == IncomingVideo.Status.NEW:
            return "Enter details", reverse("intake:register", args=[video.pk]), True
        if status.attention:
            return "Resume", reverse("intake:upload"), True
        return "Open", reverse("intake:upload"), False
    if status.to_review:
        return "Review", reverse("web:review_item", args=[status.to_review[0].pk]), True
    return "Open", reverse("web:recording", args=[rec.code]), False
