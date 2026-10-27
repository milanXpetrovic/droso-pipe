"""recording.json: everything the database knows about a recording, written next to its raw video,
so the data stays understandable without the database."""

import datetime as dt

from django.utils import timezone

SCHEMA = "recording-metadata/1"


def _date(d):
    return d.isoformat() if d else None


def recording_sidecar(rec) -> dict:
    layout = rec.layout
    return {
        "schema": SCHEMA,
        "generated_at": timezone.now().astimezone(dt.timezone.utc).isoformat(),
        "code": rec.code,
        "experiment": rec.experiment.code,
        "protocol": rec.protocol.code if rec.protocol else None,
        "protocol_duration_s": rec.protocol.duration_s if rec.protocol else None,
        "operator": rec.operator.get_username() if rec.operator else None,
        "rig": rec.rig.code,
        "camera": {"code": rec.camera.code, "model": rec.camera.model, "serial": rec.camera.serial},
        "layout": {"code": layout.code, "rows": layout.rows, "cols": layout.cols,
                   "arena_diameter_mm": layout.arena_diameter_mm, "zones": layout.zones or None},
        "started_at": rec.started_at.astimezone(dt.timezone.utc).isoformat(),
        "flies": {"condition": rec.condition.code, "genotype": rec.condition.genotype.code,
                  "condition_description": rec.condition.description, "sex": rec.sex, "n_flies": rec.n_flies,
                  "cross_date": _date(rec.cross_date), "sorting_date": _date(rec.sorting_date),
                  "incubator": rec.incubator or None},
        "window_s": [rec.window_start_s, rec.window_end_s],
        "status": rec.status,
        "notes": {"technical": rec.technical_notes or None, "behavior": rec.behavior_notes or None},
        "arenas": [
            {"index": a.index, "code": a.code, "position": list(a.position), "status": a.status,
             "reason": a.exclusion_reason or None, "window_s": [a.window_start_s, a.window_end_s],
             "groups": [{"zone": g.zone, "condition": g.condition.code, "sex": g.sex, "n_flies": g.n_flies}
                        for g in a.fly_groups.all()]}
            for a in rec.arenas.all()
        ],
        "events": [{"kind": e.kind.code, "t_s": e.t_seconds, "duration_s": e.duration_s,
                    "arena": e.arena.index if e.arena else None, "params": e.params or None}
                   for e in rec.events.all()],
    }
