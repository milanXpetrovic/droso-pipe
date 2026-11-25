# droso-pipe — project context

## Goal
End-to-end system for collecting, storing, tracking and analysing Drosophila
melanogaster social-behaviour videos. Core aim: a robust, flexible data store
that trackers, classifiers, and network/manifold analyses can plug into.
Must run automated and reliably for years. Lab-network only, no external access.
Keep things simple; avoid overengineering.

## Hardware / network (all in the lab, 1 Gbit)
- **Rig 1 / Rig 2**: each a Windows PC + Basler acA4024-29um camera, recorded via
  Basler GUI at 24 fps. Rig 1 PC is strong (RTX 4090) and should also act as a
  worker for video cropping. MATLAB available on Rig 1.
- **NAS**: TrueNAS SCALE at 10.11.48.18 (faculty network). Pool `lab`, dataset
  `videos`, shared via SMB (Windows) and NFS (milky). Central station: hosts the
  lightweight web app (Django metadata-entry app already deployed and running here).
- **milky**: Linux compute server (193.198.209.39, different subnet), strong CPU +
  GPUs. I'm a regular user, no sudo/root. MATLAB in a Docker container. Does heavy compute.
- **Raspberry Pi**: logs lab light, temperature, humidity — should be included.

## Data
- Basler filenames: `<model> (<serial>)_YYYYMMDD_HHMMSSmmm` → auto-fill camera + start time.
- Usually 4 arenas per video (2x2, 60 or 84 mm), sometimes 1 or 12 (up to 3x4).
  White arenas on black background.
- Recordings 20–30 min (may grow to 60–120), ~30 GB raw each, 20+ videos per
  strain/treatment.
- Cropping: split frame evenly along layout grid. Slight arena shift between videos
  is acceptable. No arena-detection algorithm.
- Metadata hierarchy: treatment/experiment → video → arena(s) → fly(ies).
  Previously stored as JSON next to each video from my PyQt GUI; new model should
  stay close to that, slightly refined.
- Mostly CS strain; manipulations: drug-vapor exposure (cocaine), social isolation.
  Arenas in one video usually same group, sometimes differ. Planned divided
  (wall) arena with different groups per side (e.g. females L / males R).
- Support data: isolated flies in DAMS (incubator) for sleep/activity — include it.
- Existing videos + metadata must be importable.

## Pipeline stages
1. Ingest: the user drags the video into the web **Upload** page (resumable, in chunks) and
   fills the metadata form while it uploads. No network drives for users. Copying into
   `incoming/rig1` / `incoming/rig2` still works (bulk import of old recordings).
   The real frame rate is checked at ingest (cameras may free-run faster than the file says).
2. Crop per arena (worker, e.g. Rig 1 PC).
3. Tracking: FlyTracker (MATLAB) for now, run automatically by workers; one calibration
   template per layout (made once by the admin with C:\FlyTracker, never the old 1.0.5).
   Output: trajectories/1 parquet per arena. Long-term: a better tracker (YOLO etc.) as a
   new tool, re-run over existing crops.
4. Manual review: judges a tool's RESULT, not the recording. Outcomes per arena and
   tracking result: accepted / tracking bad, video good (wait for a better tracker) /
   arena or recording unusable (excluded, video kept). Corrections (identity swaps) are
   stored as edits next to the untouched original and are also labelled data for
   training trackers.
5. Social interaction detection — criteria defined at treatment level; interaction
   networks built per arena, then analysed.
6. Behaviour classification — model in development, binary bout sequences for ~15
   behaviours. Possibly body-part annotation later.
- Existing YOLO fly-counting model (frontend: milanxpetrovic.github.io/detect-droso-frontend)
  → show per-arena fly counts in the Inbox arena view.

## Data principles
- Raw videos are never deleted or changed (read-only in raw/). Failed or bad tracking is
  never a reason to drop a recording: better trackers will come.
- Every derived file is an asset that knows the tool, version and run that made it;
  several results for the same arena (tools, versions) can coexist. Analyses use the
  chosen, accepted one.
- Crops may be deleted to save space: they can always be made again from raw.

## UI / ops
- English UI, app `web`: Recordings (status of every recording in 5 steps: Upload · Check ·
  Arenas · Tracking · Review, grouped by what needs doing), Recording detail, Review (one arena
  at a time: paths, automatic quality checks, accept / tracking bad / unusable), Experiments
  (usable recordings per condition vs target), Machines, Settings. Upload page in `intake`.
- Design reference: https://claude.ai/artifact/XSERZQsdNTW3tyfc4kZWTi (7 screens).
- Built for ~50+ recordings and 3 users (admin + 2 biotechnologists); Django admin stays as the
  admin's fallback, not something users need.
- Optional Discord bot for status messages.

## Repo
github.com/milanXpetrovic/droso-pipe. Done: metadata model, browser upload, ingest to raw/
on the NAS with frame-rate check, worker API, crop and FlyTracker tools, quality checks,
review level 1, status pages. Not started: identity-swap correction (review level 2), video
with fly labels in review, interaction detection, classification, YOLO counts, sensor/DAM
import, legacy JSON import.
Deploy: push a tag `app-vX.Y.Z` -> CI tests and builds the image -> set that tag in TrueNAS.

## Users
Biotechnologists with no technical background. Everything they do must work from the browser,
with no shells, drives or settings; one-time setup is the admin's job.