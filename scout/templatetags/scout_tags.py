from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def score_band(score, threshold=65):
    if score is None:
        return "none"
    threshold = int(threshold or 65)
    if score >= max(threshold + 15, 85):
        return "top"
    if score >= threshold:
        return "good"
    if score >= 40:
        return "mid"
    return "low"


@register.filter
def ago(dt):
    """Compact relative time: 3h, 2d, 5w."""
    if not dt:
        return "—"
    secs = (timezone.now() - dt).total_seconds()
    if secs < 0:
        return "now"
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit}"
    return "now"


@register.filter
def days_until(d):
    if not d:
        return None
    return (d - timezone.localdate()).days


@register.filter
def initials(name):
    parts = [p for p in (name or "?").replace("-", " ").split() if p[:1].isalnum()]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


@register.filter
def source_label(key):
    from scout.services.sources import get_sources
    cls = get_sources().get(key)
    return cls.label if cls else key


@register.filter
def bar(value, width=10):
    """ASCII meter: 72 -> ███████░░░"""
    width = int(width)
    try:
        v = max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return "░" * width
    n = round(v * width / 100)
    return "█" * n + "░" * (width - n)


@register.filter
def ratio_bar(value, total):
    """ASCII meter for value/total (e.g. 12/40 used)."""
    try:
        pct = 100 * int(value) / max(1, int(total))
    except (TypeError, ValueError):
        pct = 0
    return bar(pct, 20)


_ICONS = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "external": '<path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
    "pin": '<path d="M12 21s-7-6.2-7-11a7 7 0 1 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>',
    "money": '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "briefcase": '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    "alert": '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4"/><path d="M12 17.5v.5"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.5v.5"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.5-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.5 4.5L20 16"/><path d="M20 20v-4h-4"/>',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
    "inbox": '<path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5 5h14l2 8v6H3v-6l2-8z"/>',
    "download": '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>',
    "pen": '<path d="M4 20h4L19 9l-4-4L4 16v4z"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18"/>',
}


@register.simple_tag
def icon(name):
    from django.utils.safestring import mark_safe
    body = _ICONS.get(name, "")
    return mark_safe(
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{body}</svg>'
    )


@register.filter
def pct(value, total):
    try:
        return max(0, min(100, round(100 * int(value) / max(1, int(total)))))
    except (TypeError, ValueError):
        return 0


@register.simple_tag
def asset(path):
    """Static URL with a version stamp, so browsers always load the newest CSS/JS after an update."""
    import os
    from django.contrib.staticfiles import finders
    from django.templatetags.static import static
    found = finders.find(path)
    try:
        version = int(os.path.getmtime(found)) if found else 0
    except OSError:
        version = 0
    return f"{static(path)}?v={version}"
