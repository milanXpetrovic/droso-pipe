"""File helpers shared by ingest and commit: checksummed copies, atomic JSON, safe paths."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath

from django.conf import settings

CHUNK = 8 * 1024 * 1024


def root() -> Path:
    return Path(settings.VIDEOS_ROOT)


def resolve(rel_path: str | PurePosixPath) -> Path:
    """Absolute path under the videos root; refuses anything that escapes it."""
    base = root().resolve()
    path = (base / str(rel_path)).resolve()
    if base != path and base not in path.parents:
        raise ValueError(f"Path outside the videos root: {rel_path}")
    return path


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def copy_with_sha256(src: Path, dst: Path) -> tuple[str, int]:
    h, size = hashlib.sha256(), 0
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while chunk := fi.read(CHUNK):
            h.update(chunk)
            fo.write(chunk)
            size += len(chunk)
        fo.flush()
        os.fsync(fo.fileno())
    return h.hexdigest(), size


def write_json(path: Path, data: dict, mode: int = 0o440):
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.chmod(mode)
    os.replace(tmp, path)
