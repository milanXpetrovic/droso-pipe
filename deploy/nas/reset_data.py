"""Reset droso-pipe to an empty state, keeping what is set up once.

Keeps:    users, workers (and their tokens), tools, asset types, catalogues
          (genotypes, conditions, protocols, rigs, cameras, layouts, event kinds).
Deletes:  experiments, recordings, arenas, fly groups, events, incoming videos, runs, assets,
          tracker templates, and their history; on disk the CONTENTS of derived/, staging/,
          support/flytracker/templates/ (the folders stay: staging/ has permissions set by hand
          so that workers can write into it, and nobody but the admin can recreate it).
Raw videos are moved back to incoming/<rig>/ under their original Basler names (nothing is lost);
raw/ is emptied only when every video was moved.

Stop the workers first. Then, inside the app container:

    python manage.py shell < reset_data.py               # dry run: only prints what would happen
    RESET=yes python manage.py shell < reset_data.py     # does it
"""

import os
import shutil

from django.db import transaction

from core.models import Asset, Run, TrackerTemplate
from core.storage import resolve, root
from domain.models import Arena, Event, Experiment, FlyGroup, Recording
from intake.models import IncomingVideo

DO = os.environ.get("RESET") == "yes"
print("=== RESET ===" if DO else "=== DRY RUN (set RESET=yes to really do it) ===")

# 1. where every raw video goes back to
moves = []
for a in Asset.objects.filter(type__code="raw_video").select_related("recording__rig"):
    src = resolve(a.rel_path)
    rig_dir = a.recording.rig.incoming_dir if a.recording else ""
    if not rig_dir:
        raise SystemExit(f"{a.rel_path}: rig without incoming_dir, cannot decide where it goes. Nothing changed.")
    dest = resolve(f"incoming/{rig_dir}/{a.meta.get('source_file_name') or src.name}")
    if not src.exists():
        raise SystemExit(f"{src} is missing on disk. Nothing changed.")
    if dest.exists():
        raise SystemExit(f"{dest} already exists. Nothing changed.")
    moves.append((src, dest))
    print(f"move  {src}  ->  {dest}")

# 2. database: children before parents (most links are PROTECT); domain models soft-delete, so hard_delete
counts = {
    "tracker templates": TrackerTemplate.objects.count(), "assets": Asset.objects.count(),
    "runs": Run.objects.count(), "incoming videos": IncomingVideo.objects.count(),
    "events": Event.all_objects.count(), "fly groups": FlyGroup.all_objects.count(),
    "arenas": Arena.all_objects.count(), "recordings": Recording.all_objects.count(),
    "experiments": Experiment.all_objects.count(),
}
for k, v in counts.items():
    print(f"delete {v:5d} {k}")

if DO:
    with transaction.atomic():
        TrackerTemplate.objects.all().delete()
        Asset.objects.all().delete()
        Run.objects.all().delete()
        IncomingVideo.objects.all().delete()
        for m in (Event, FlyGroup, Arena, Recording, Experiment):
            m.all_objects.all().hard_delete()
            m.history.all().delete()
    print("database: done")

# 3. disk
moved_all = True
for src, dest in moves:
    if not DO:
        continue
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dest)
        dest.chmod(0o660)                       # was read-only in raw/; ingest must be able to remove it later
        print(f"moved {dest.name}")
    except OSError as e:
        moved_all = False
        print(f"FAILED to move {src}: {e}")

dirs = ["derived", "staging", "support/flytracker/templates"] + (["raw"] if moved_all else [])
for d in dirs:
    p = root() / d
    print(f"empty {p}/ ({'exists' if p.exists() else 'not there'}; the folder itself stays)")
    if DO and p.exists():
        for child in p.iterdir():
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
if DO and not moved_all:
    print("raw/ was KEPT because some videos could not be moved; see FAILED lines above.")
print("done" if DO else "dry run finished, nothing changed")
