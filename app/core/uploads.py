"""Files people upload through the admin (calibration templates, ...) become assets like any other."""

import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath

from django.db import transaction
from django.utils import timezone

from .formats import asset_type
from .models import Asset, Run, Tool
from .storage import resolve

UPLOAD = {"code": "upload", "version": "1.0.0", "description": "A file uploaded by a person through the admin."}


def store_upload(uploaded, rel_dir: str, file_name: str, type_key, meta: dict, user, warnings=None) -> Asset:
    """Copy an uploaded file into the store (read-only, checksummed) and register it."""
    h = hashlib.sha256()
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        for chunk in uploaded.chunks():
            h.update(chunk)
            tmp.write(chunk)
    sha = h.hexdigest()
    rel = PurePosixPath(rel_dir) / f"{Path(file_name).stem}-{sha[:8]}{Path(file_name).suffix}"
    dest = resolve(rel)
    old_umask = os.umask(0o027)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            part = dest.with_name(f".{dest.name}.part")
            with open(tmp.name, "rb") as fi, open(part, "wb") as fo:
                fo.write(fi.read())
            part.chmod(0o440)
            os.replace(part, dest)
    finally:
        os.umask(old_umask)
        os.unlink(tmp.name)
    existing = Asset.objects.filter(rel_path=str(rel)).first()
    if existing:
        return existing
    tool, _ = Tool.objects.get_or_create(code=UPLOAD["code"], version=UPLOAD["version"],
                                         defaults={"description": UPLOAD["description"]})
    with transaction.atomic():
        run = Run.objects.create(tool=tool, status=Run.Status.DONE, worker=user.get_username() if user else "",
                                 started_at=timezone.now(), finished_at=timezone.now(),
                                 params={"original_name": file_name})
        return Asset.objects.create(type=asset_type(type_key), rel_path=str(rel), sha256=sha,
                                    size_bytes=dest.stat().st_size, meta=meta, warnings=warnings or [],
                                    produced_by=run)
