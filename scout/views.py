from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import JobDraftForm, JobNotesForm, OutreachForm, OutreachMessageForm, ProfileForm, SettingsForm
from .models import ActivityLog, Job, LLMCall, OutreachTarget, Profile, ScoutSettings, SourceState
from .services import drafting, outreach, scoring
from .services.activity import log
from .services.claude_client import ClaudeError
from .services.collect import fetch_all, refilter_unscored
from .services.profile_seed import apply_cv_seed, build_prompt_profile
from .services.sources import get_sources

PIPELINE = [Job.Status.SHORTLISTED, Job.Status.APPLIED, Job.Status.INTERVIEWING, Job.Status.OFFER,
            Job.Status.REJECTED, Job.Status.GHOSTED]
SORTS = {
    "score": [F("score").desc(nulls_last=True), F("posted_at").desc(nulls_last=True)],
    "-score": [F("score").asc(nulls_last=True), F("posted_at").desc(nulls_last=True)],
    "posted": [F("posted_at").desc(nulls_last=True)],
    "-posted": [F("posted_at").asc(nulls_last=True)],
    "company": ["company", "title"],
    "found": ["-fetched_at"],
}


def owner_only(view):
    """Optional login gate (SCOUT_REQUIRE_LOGIN=true in .env)."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if settings.SCOUT_REQUIRE_LOGIN and not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        return view(request, *args, **kwargs)
    return wrapped


def _back(request, fallback):
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(fallback)


# ------------------------------------------------------------------ overview

@owner_only
def overview(request):
    s = ScoutSettings.get_solo()
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    applied_ever = Job.objects.filter(applied_at__isnull=False)
    responded = applied_ever.filter(status__in=[Job.Status.INTERVIEWING, Job.Status.OFFER, Job.Status.REJECTED]).count()
    applied_count = applied_ever.count()
    stats = {
        "new_today": Job.objects.filter(passed_filter=True, fetched_at__date=today).count(),
        "shortlisted": Job.objects.filter(status=Job.Status.SHORTLISTED).count(),
        "applied_week": Job.objects.filter(applied_at__date__gte=week_start).count(),
        "interviews": Job.objects.filter(status=Job.Status.INTERVIEWING).count(),
        "followups_due": Job.objects.filter(status=Job.Status.APPLIED, follow_up_on__lte=today).count(),
        "response_rate": round(100 * responded / applied_count) if applied_count else None,
        "responded": responded,
        "applied_total": applied_count,
    }
    usage = {
        "scored": scoring.scored_today(), "score_cap": s.daily_score_cap,
        "drafted": drafting.drafted_today(), "draft_cap": s.daily_draft_cap,
        "calls": LLMCall.objects.filter(created_at__date=today).count(),
        "failed_calls": LLMCall.objects.filter(created_at__date=today, ok=False).count(),
        "awaiting_score": scoring.pending_jobs().count(),
        "awaiting_draft": drafting.draftable_jobs(s.score_threshold).count(),
    }
    top = (Job.objects.filter(score__gte=s.score_threshold, status__in=[Job.Status.NEW, Job.Status.SHORTLISTED])
           .order_by("-score", "-posted_at")[:8])
    followups = Job.objects.filter(status=Job.Status.APPLIED, follow_up_on__lte=today).order_by("follow_up_on")[:8]
    states = {st.key: st for st in SourceState.objects.all()}
    sources = [{"key": k, "label": c.label, "enabled": s.source_enabled(k), "state": states.get(k)}
               for k, c in get_sources().items()]
    return render(request, "scout/overview.html", {
        "stats": stats, "usage": usage, "top": top, "followups": followups, "sources": sources,
        "activity": ActivityLog.objects.select_related("job")[:8], "threshold": s.score_threshold,
    })


@owner_only
@require_POST
def run_action(request):
    """Small, manual runs from the dashboard. Bigger runs belong in run_scout."""
    action = request.POST.get("action")
    try:
        if action == "fetch":
            res = fetch_all(force=request.POST.get("force") == "1")
            kept = sum(r["passed"] for r in res.values() if isinstance(r, dict))
            errors = [k for k, r in res.items() if str(r).startswith("error")]
            msg = f"Fetch done: {kept} new jobs kept."
            if errors:
                msg += f" Errors from: {', '.join(errors)} (see Activity)."
            not_due = [k for k, r in res.items() if str(r).startswith("not due")]
            if not_due and not kept:
                msg += " Some sources aren't due yet (rate-limit spacing)."
            messages.success(request, msg)
        elif action == "score":
            st = scoring.score_pending(limit=int(request.POST.get("limit", 5)))
            messages.success(request, f"Scored {st['scored']} jobs ({st['calls']} Claude call). {st['stopped']}".strip())
        elif action == "draft":
            st = drafting.draft_pending(limit=int(request.POST.get("limit", 3)))
            messages.success(request, f"Drafted {st['drafted']} notes. {st['stopped']}".strip())
        elif action == "refilter":
            n = refilter_unscored()
            messages.success(request, f"Re-applied filters: {n} jobs changed.")
        else:
            messages.error(request, "Unknown action.")
    except ClaudeError as exc:
        messages.error(request, f"Claude: {exc}")
    return _back(request, reverse("scout:overview"))


# ------------------------------------------------------------------ jobs

@owner_only
def jobs(request):
    s = ScoutSettings.get_solo()
    qs = Job.objects.all()
    view = request.GET.get("view", "open")
    if view == "open":
        qs = qs.filter(passed_filter=True).exclude(status=Job.Status.SKIPPED)
    elif view == "filtered":
        qs = qs.filter(passed_filter=False)
    elif view == "skipped":
        qs = qs.filter(status=Job.Status.SKIPPED)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(company__icontains=q) | Q(description__icontains=q))
    status = request.GET.get("status", "")
    if status in Job.Status.values:
        qs = qs.filter(status=status)
    source = request.GET.get("source", "")
    if source:
        qs = qs.filter(source=source)
    min_score = request.GET.get("min", "")
    if min_score.isdigit():
        qs = qs.filter(score__gte=int(min_score))
    if request.GET.get("drafted") == "1":
        qs = qs.filter(drafted_at__isnull=False)
    if request.GET.get("unscored") == "1":
        qs = qs.filter(scored_at__isnull=True)
    if request.GET.get("due") == "1":
        qs = qs.filter(status=Job.Status.APPLIED, follow_up_on__lte=timezone.localdate())
    if request.GET.get("flags") == "1":
        qs = qs.filter(~Q(scam_flags=[]) | ~Q(ai_red_flags=[]))
    sort = request.GET.get("sort", "score")
    if sort not in SORTS:
        sort = "score"
    qs = qs.order_by(*SORTS[sort])
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    sel = request.GET.get("job", "")
    selected = Job.objects.filter(pk=int(sel)).first() if sel.isdigit() else None
    explicit = selected is not None
    if selected is None and page.object_list:
        selected = page.object_list[0]
    counts = {
        "open": Job.objects.filter(passed_filter=True).exclude(status=Job.Status.SKIPPED).count(),
        "filtered": Job.objects.filter(passed_filter=False).count(),
        "skipped": Job.objects.filter(status=Job.Status.SKIPPED).count(),
    }
    return render(request, "scout/jobs.html", {
        "page": page, "view": view, "q": q, "status": status, "source": source, "min_score": min_score,
        "sort": sort, "statuses": Job.Status.choices, "sources": [(k, c.label) for k, c in get_sources().items()],
        "threshold": s.score_threshold, "counts": counts,
        "selected": selected, "explicit": explicit,
        "selected_project": Profile.get_solo().project_by_key(selected.chosen_project)
        if selected and selected.chosen_project else None,
    })


@owner_only
def job_detail(request, pk):
    job = get_object_or_404(Job, pk=pk)
    s = ScoutSettings.get_solo()
    profile = Profile.get_solo()
    project = profile.project_by_key(job.chosen_project) if job.chosen_project else None
    others = [{"source": x.get("source"), "label": get_sources().get(x.get("source")).label
               if get_sources().get(x.get("source")) else x.get("source"), "url": x.get("url")}
              for x in job.also_seen_on or []]
    return render(request, "scout/job_detail.html", {
        "job": job, "draft_form": JobDraftForm(instance=job), "notes_form": JobNotesForm(instance=job),
        "statuses": Job.Status.choices, "threshold": s.score_threshold, "project": project,
        "source_label": get_sources()[job.source].label if job.source in get_sources() else job.source,
        "also_seen": others, "activity": job.activity.all()[:12],
    })


@owner_only
@require_POST
def job_status(request, pk):
    job = get_object_or_404(Job, pk=pk)
    new = request.POST.get("status")
    if new not in Job.Status.values:
        messages.error(request, "Unknown status.")
        return _back(request, job.get_absolute_url())
    old = job.set_status(new)
    if old != new:
        extra = f" Follow up on {job.follow_up_on:%a %d %b}." if new == Job.Status.APPLIED else ""
        log("status", f"{job.title} @ {job.company}: {Job.Status(old).label} → {job.get_status_display()}", job=job)
        if request.headers.get("x-requested-with") == "fetch":
            return JsonResponse({"ok": True, "status": new, "label": job.get_status_display(),
                                 "follow_up_on": job.follow_up_on.isoformat() if job.follow_up_on else None})
        messages.success(request, f"Moved to {job.get_status_display()}.{extra}")
    return _back(request, job.get_absolute_url())


@owner_only
@require_POST
def job_save(request, pk):
    job = get_object_or_404(Job, pk=pk)
    part = request.POST.get("part")
    form = (JobDraftForm if part == "draft" else JobNotesForm)(request.POST, instance=job)
    if form.is_valid():
        job = form.save()
        if part == "draft":
            job.draft_warnings = drafting.review_draft(job.cover_note, job.opener,
                                                       Profile.get_solo().prompt_profile, job)
            job.save(update_fields=["draft_warnings", "updated_at"])
        if request.headers.get("x-requested-with") == "fetch":
            return JsonResponse({"ok": True, "warnings": job.draft_warnings})
        messages.success(request, "Saved.")
    else:
        if request.headers.get("x-requested-with") == "fetch":
            return JsonResponse({"ok": False, "errors": form.errors}, status=400)
        messages.error(request, "Couldn't save — check the fields.")
    return redirect(job.get_absolute_url())


@owner_only
@require_POST
def job_action(request, pk):
    job = get_object_or_404(Job, pk=pk)
    action = request.POST.get("action")
    try:
        if action == "redraft":
            drafting.redraft_job(job)
            messages.success(request, "New draft ready. Read it before you use it.")
        elif action == "rescore":
            scoring.score_one(job)
            messages.success(request, f"Re-scored: {job.score}.")
        elif action == "followed_up":
            s = ScoutSettings.get_solo()
            job.followed_up_at = timezone.now()
            job.follow_up_on = timezone.localdate() + timedelta(days=s.followup_days)
            job.save(update_fields=["followed_up_at", "follow_up_on", "updated_at"])
            log("status", f"Followed up on {job.title} @ {job.company}", job=job)
            messages.success(request, f"Logged. Next follow-up {job.follow_up_on:%a %d %b}.")
        elif action == "restore":
            job.passed_filter = True
            job.filter_reasons = []
            job.set_status(Job.Status.NEW, save=False)
            job.save()
            log("filter", f"Manually restored {job.title} @ {job.company}", job=job)
            messages.success(request, "Restored. It will be scored on the next run.")
        else:
            messages.error(request, "Unknown action.")
    except Exception as exc:  # surface Claude/parse errors to the page
        messages.error(request, f"{exc}")
    return redirect(job.get_absolute_url())


# ------------------------------------------------------------------ pipeline

@owner_only
def pipeline(request):
    columns = []
    for st in PIPELINE:
        items = Job.objects.filter(status=st).order_by("follow_up_on", "-score")
        columns.append({"key": st, "label": Job.Status(st).label, "jobs": items[:50], "count": items.count()})
    new_ready = (Job.objects.filter(status=Job.Status.NEW, score__gte=ScoutSettings.get_solo().score_threshold)
                 .order_by("-score")[:12])
    return render(request, "scout/pipeline.html", {
        "columns": columns, "new_ready": new_ready, "statuses": Job.Status.choices,
        "today": timezone.localdate(),
        "skipped": Job.objects.filter(status=Job.Status.SKIPPED).count(),
    })


# ------------------------------------------------------------------ outreach

@owner_only
def outreach_list(request):
    form = OutreachForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            t = form.save()
            log("outreach", f"Added {t.company_name} to outreach list")
            messages.success(request, f"Added {t.company_name}.")
            return redirect("scout:outreach")
        messages.error(request, "Check the form.")
    targets = OutreachTarget.objects.all()
    status = request.GET.get("status")
    if status in OutreachTarget.Status.values:
        targets = targets.filter(status=status)
    s = ScoutSettings.get_solo()
    return render(request, "scout/outreach.html", {
        "form": form, "targets": targets, "status": status, "statuses": OutreachTarget.Status.choices,
        "to_draft": OutreachTarget.objects.filter(status=OutreachTarget.Status.TO_DRAFT).count(),
        "drafted_today": outreach.drafted_today(), "cap": s.daily_outreach_cap,
        "open_form": request.method == "POST" or not OutreachTarget.objects.exists(),
    })


@owner_only
def outreach_detail(request, pk):
    t = get_object_or_404(OutreachTarget, pk=pk)
    form = OutreachMessageForm(request.POST or None, instance=t)
    edit = OutreachForm(instance=t, prefix="e")
    if request.method == "POST":
        if request.POST.get("part") == "details":
            edit = OutreachForm(request.POST, instance=t, prefix="e")
            if edit.is_valid():
                edit.save()
                messages.success(request, "Details saved.")
                return redirect("scout:outreach_detail", pk=t.pk)
        elif form.is_valid():
            form.save()
            messages.success(request, "Saved.")
            return redirect("scout:outreach_detail", pk=t.pk)
    profile = Profile.get_solo()
    return render(request, "scout/outreach_detail.html", {
        "t": t, "form": form, "edit": edit, "statuses": OutreachTarget.Status.choices,
        "project": profile.project_by_key(t.chosen_project) if t.chosen_project else None,
    })


@owner_only
@require_POST
def outreach_action(request, pk=None):
    action = request.POST.get("action")
    try:
        if action == "draft_all":
            done = outreach.draft_outreach(OutreachTarget.objects.filter(status=OutreachTarget.Status.TO_DRAFT))
            messages.success(request, f"Drafted {len(done)} messages. Nothing was sent.")
            return redirect("scout:outreach")
        t = get_object_or_404(OutreachTarget, pk=pk)
        if action == "draft":
            done = outreach.draft_outreach([t])
            messages.success(request, "Draft ready." if done else "Claude didn't return a message — try again.")
        elif action == "status":
            new = request.POST.get("status")
            if new in OutreachTarget.Status.values:
                t.status = new
                if new == OutreachTarget.Status.SENT and not t.sent_at:
                    t.sent_at = timezone.now()
                t.save()
                log("outreach", f"{t.company_name}: marked {t.get_status_display()}")
                messages.success(request, f"Marked {t.get_status_display()}.")
        elif action == "delete":
            name = t.company_name
            t.delete()
            messages.success(request, f"Removed {name}.")
            return redirect("scout:outreach")
    except ClaudeError as exc:
        messages.error(request, f"Claude: {exc}")
    return _back(request, reverse("scout:outreach_detail", args=[pk]) if pk else reverse("scout:outreach"))


# ------------------------------------------------------------------ settings & activity

@owner_only
def settings_view(request):
    s = ScoutSettings.get_solo()
    profile = Profile.get_solo()
    form = SettingsForm(instance=s)
    pform = ProfileForm(instance=profile)
    if request.method == "POST":
        part = request.POST.get("part")
        if part == "settings":
            form = SettingsForm(request.POST, instance=s)
            if form.is_valid():
                old_filters = (s.keywords, s.allowed_locations, s.block_senior_titles, s.max_years_required, s.max_age_days, s.excluded_titles, s.min_score)
                s = form.save()
                log("system", "Settings updated")
                new_filters = (s.keywords, s.allowed_locations, s.block_senior_titles, s.max_years_required, s.max_age_days, s.excluded_titles, s.min_score)
                msg = "Settings saved."
                if old_filters != new_filters:
                    n = refilter_unscored()
                    msg += f" Filters changed, so I re-checked unscored jobs ({n} changed)."
                messages.success(request, msg)
                return redirect("scout:settings")
            messages.error(request, "Some settings need fixing.")
        elif part == "profile":
            pform = ProfileForm(request.POST, instance=profile)
            if pform.is_valid():
                pform.save()
                log("system", "Profile text edited")
                messages.success(request, "Profile saved. Claude will use this text from now on.")
                return redirect("scout:settings")
        elif part == "rebuild":
            apply_cv_seed(profile)
            log("system", "Profile rebuilt from CV seed")
            messages.success(request, "Profile rebuilt from your CV.")
            return redirect("scout:settings")
    states = {st.key: st for st in SourceState.objects.all()}
    sources = [{"key": k, "label": c.label, "homepage": c.homepage, "interval": c.min_interval,
                "state": states.get(k)} for k, c in get_sources().items()]
    return render(request, "scout/settings.html", {
        "form": form, "pform": pform, "sources": sources, "profile": profile,
        "env_model": settings.LLM_MODEL, "cli": settings.CLAUDE_CLI,
        "cv_preview_differs": profile.prompt_profile.strip() != build_prompt_profile().strip(),
    })


@owner_only
def activity(request):
    qs = ActivityLog.objects.select_related("job")
    kind = request.GET.get("kind", "")
    if kind in ActivityLog.Kind.values:
        qs = qs.filter(kind=kind)
    page = Paginator(qs, 60).get_page(request.GET.get("page"))
    calls = LLMCall.objects.all()[:15]
    by_day = (LLMCall.objects.filter(created_at__gte=timezone.now() - timedelta(days=7))
              .annotate(day=TruncDate("created_at")).values("day")
              .annotate(n=Count("id"), items=Sum("items")).order_by("-day"))
    return render(request, "scout/activity.html", {
        "page": page, "kind": kind, "kinds": ActivityLog.Kind.choices, "calls": calls, "by_day": list(by_day),
    })
