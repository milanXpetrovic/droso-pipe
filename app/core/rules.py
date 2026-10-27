"""What should run next. Idempotent: called after ingest and on every keeper cycle."""

from domain.models import Recording

from .models import Asset, Run
from .tools import CROP, tool



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
    return created
