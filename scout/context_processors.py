from django.utils import timezone


def nav(request):
    """Counts for the header. Only runs on dashboard pages."""
    match = getattr(request, "resolver_match", None)
    if not match or match.app_name != "scout":
        return {}
    from .models import Job, OutreachTarget, ScoutSettings

    today = timezone.localdate()
    s = ScoutSettings.get_solo()
    return {
        "nav_due": Job.objects.filter(status=Job.Status.APPLIED, follow_up_on__lte=today).count(),
        "nav_new": Job.objects.filter(status=Job.Status.NEW, passed_filter=True).count(),
        "nav_outreach": OutreachTarget.objects.filter(status__in=["to_draft", "drafted"]).count(),
        "nav_scored": Job.objects.filter(scored_at__date=today).count(),
        "nav_score_cap": s.daily_score_cap,
        "nav_section": match.url_name,
    }
