from django import template

register = template.Library()


@register.filter
def hms(seconds):
    """1872.4 -> '31:12', 3725 -> '1:02:05'."""
    if seconds in (None, ""):
        return "—"
    s = int(round(float(seconds)))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
