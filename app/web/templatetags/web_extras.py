from django import template

register = template.Library()


@register.filter
def pct(fraction, digits=1):
    """0.0123 -> '1.2 %'."""
    if fraction in (None, ""):
        return "—"
    return f"{100 * float(fraction):.{int(digits)}f} %"


@register.filter
def gb(n):
    if not n:
        return "0 GB"
    n = float(n)
    return f"{n / 1e12:.2f} TB" if n >= 1e12 else f"{n / 1e9:.1f} GB"
