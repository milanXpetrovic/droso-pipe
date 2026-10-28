"""What should run next. Idempotent: called after ingest and on every keeper cycle."""

from domain.models import Arena, Recording

from .models import Asset, Run, TrackerTemplate
from .tools import CROP, FLYTRACKER, tool



def plan_runs() -> int:
    """raw_video without a crop run -> new pending crop run. Failed runs are left for a person to requeue."""
    created = 0
    crop = tool(CROP)
    raws = (Asset.objects.filter(type__code="raw_video", status=Asset.Status.OK)
            .exclude(recording__status=Recording.Status.REJECTED).select_related("recording"))
    for raw in raws:
        if Run.objects.filter(tool=crop, inputs=raw).exclude(status=Run.Status.CANCELLED).exists():
            continue
        run = Run.objects.create(tool=crop, recording=raw.recording)
        run.inputs.add(raw)
        created += 1
    return created + plan_tracking()


def plan_tracking() -> int:
    """crop_video of a valid arena, with an active template for its layout -> flytracker run."""
    created = 0
    flytracker = tool(FLYTRACKER)
    templates = {t.layout_id: t for t in TrackerTemplate.objects.filter(active=True).select_related("asset")}
    crops = (Asset.objects.filter(type__code="crop_video", status=Asset.Status.OK, arena__status=Arena.Status.OK)
             .exclude(recording__status=Recording.Status.REJECTED).select_related("recording", "arena"))
    for crop in crops:
        template = templates.get(crop.recording.layout_id)
        if template is None:
            continue                                    # shown on the recording page as "no tracker template"
        if Run.objects.filter(tool=flytracker, inputs=crop).exclude(status=Run.Status.CANCELLED).exists():
            continue
        run = Run.objects.create(tool=flytracker, recording=crop.recording, arena=crop.arena)
        run.inputs.add(crop, template.asset)
        created += 1
    return created
