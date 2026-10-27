"""Read metadata from video file names written by Basler recording software.

    Basler acA4024-29um (23517383)_20260203_111507240.mp4
    └ model ──────────┘ └ serial ┘ └ date ┘ └ time + ms ┘
The time is the rig PC's local clock (Europe/Zagreb).
"""

import datetime as dt
import re
from zoneinfo import ZoneInfo

RIG_TZ = ZoneInfo("Europe/Zagreb")
BASLER = re.compile(r"^(?P<model>.+?) \((?P<serial>\d+)\)_(?P<date>\d{8})_(?P<time>\d{6})(?P<ms>\d{3})?")


def parse_basler_name(name: str) -> dict | None:
    m = BASLER.match(name)
    if not m:
        return None
    try:
        t = dt.datetime.strptime(m["date"] + m["time"], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return {
        "camera_model": m["model"].removeprefix("Basler ").strip(),
        "camera_serial": m["serial"],
        "started_at": t.replace(microsecond=int(m["ms"] or 0) * 1000, tzinfo=RIG_TZ),
    }
