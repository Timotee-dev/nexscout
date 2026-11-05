"""
Hands-free applying. Runs inside every autopilot cycle when Settings → "Apply automatically" is on.

For each new job at or above your auto-apply score (best first) until today's limit:
  * email jobs -> sent from your Gmail with the tailored CV
  * form jobs  -> filled in a hidden browser and submitted, but only if
       - you've confirmed your answer sheet in Settings,
       - every required question is answered from your profile / answer sheet,
       - there's no visible CAPTCHA (NexScout never tries to solve one),
     and a confirmation page is seen afterwards. A screenshot is kept as proof.
Anything that can't be done safely is left in Apply → Needs your review with the reason.
"""
import os
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from scout.models import Job, ScoutSettings

from . import mailer
from .activity import log
from .applier import _queue

BLOCKED_STATES = (Job.ApplyQueue.REVIEW, Job.ApplyQueue.QUEUED)


def auto_apply_problems(job, s=None):
    """Every reason autopilot would NOT apply to this job by itself. Empty list = OK to apply."""
    s = s or ScoutSettings.get_solo()
    problems = []
    if job.email_sent_at or job.form_submitted_at:
        problems.append("already applied")
    if job.status not in (Job.Status.NEW, Job.Status.SHORTLISTED):
        problems.append("you've already moved this job")
    if not job.passed_filter:
        problems.append("filtered out by your rules")
    if job.score is None or job.score < s.auto_send_min_score:
        problems.append(f"score below {s.auto_send_min_score}")
    if job.open_to_nigeria == Job.NigeriaFit.NO:
        problems.append("not open to Nigeria")
    if job.scam_flags or job.ai_red_flags:
        problems.append("scam warnings")
    if not job.cover_note or not job.drafted_at:
        problems.append("no draft yet")
    if job.draft_warnings:
        problems.append("draft has warnings to check")
    if job.apply_queue in BLOCKED_STATES or job.apply_error:
        problems.append("waiting for you in the Apply queue")
    if job.company_key and Job.objects.filter(company_key=job.company_key).filter(
            Q(email_sent_at__gte=timezone.now() - timedelta(days=30)) |
            Q(form_submitted_at__gte=timezone.now() - timedelta(days=30))).exclude(pk=job.pk).exists():
        problems.append("already applied to this company in the last 30 days")
    if job.apply_email:
        domain = job.apply_email.partition("@")[2]
        if any(domain.startswith(p) or p in domain for p in mailer.PERSONAL_DOMAINS):
            problems.append("goes to a personal email address")
        if Job.objects.filter(apply_email__iexact=job.apply_email, email_sent_at__isnull=False).exclude(pk=job.pk).exists():
            problems.append("you already emailed this address")
    else:
        if not (job.apply_url or job.url):
            problems.append("no application link")
        if not s.answers_confirmed:
            problems.append("answer sheet not confirmed in Settings")
    return problems


def playwright_available():
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def candidates(s):
    return (Job.objects.filter(status__in=[Job.Status.NEW, Job.Status.SHORTLISTED], passed_filter=True,
                               score__gte=s.auto_send_min_score, email_sent_at__isnull=True,
                               form_submitted_at__isnull=True)
            .exclude(cover_note="")
            .order_by("-score", "-posted_at"))


def run_auto_apply(deadline=None, out=print, browser_factory=None, ask=None):
    """Apply to as many safe jobs as today's limit allows. Returns stats."""
    s = ScoutSettings.get_solo()
    stats = {"emailed": 0, "forms": 0, "needs_you": 0, "skipped": 0, "followups": 0}
    if not s.auto_send:
        return stats
    forms = []
    for job in candidates(s):
        if mailer.sent_today() + len(forms) >= s.daily_send_cap:
            break
        if deadline and time.monotonic() > deadline:
            break
        if auto_apply_problems(job, s):
            stats["skipped"] += 1
            continue
        if job.apply_email:
            if not (s.send_enabled and mailer.email_ready()):
                stats["skipped"] += 1
                continue
            try:
                mailer.send_application(job, by="autopilot")
                stats["emailed"] += 1
            except mailer.SendBlocked as exc:
                _queue(job, Job.ApplyQueue.REVIEW, f"Autopilot couldn't send: {exc}")
                stats["needs_you"] += 1
        else:
            forms.append(job)

    if forms and playwright_available():
        _apply_forms(forms, s, stats, deadline, out, browser_factory, ask)
    elif forms:
        stats["skipped"] += len(forms)

    if s.auto_followup and s.send_enabled and mailer.email_ready():
        due = Job.objects.filter(status=Job.Status.APPLIED, email_sent_at__isnull=False,
                                 followup_email_sent_at__isnull=True, follow_up_on__lte=timezone.localdate())
        for job in due:
            try:
                mailer.send_followup(job, by="autopilot")
                stats["followups"] += 1
            except mailer.SendBlocked:
                pass
    return stats


def _open_browser(pw):
    context_opts = {"viewport": {"width": 1280, "height": 1000}}
    for channel in ("chrome", "msedge", None):
        try:
            browser = (pw.chromium.launch(channel=channel, headless=True) if channel
                       else pw.chromium.launch(headless=True))
            return browser, context_opts
        except Exception:
            continue
    raise RuntimeError("No browser available. Install Chrome, or run: .venv\\Scripts\\playwright install chromium")


def _apply_forms(jobs, s, stats, deadline, out, browser_factory=None, ask=None):
    from playwright.sync_api import sync_playwright

    from . import autofill
    from .cv_builder import build_cv, cv_for

    os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    with sync_playwright() as pw:
        try:
            browser, opts = browser_factory(pw) if browser_factory else _open_browser(pw)
        except Exception as exc:
            log("error", f"Hands-free applying couldn't start a browser: {exc}")
            stats["skipped"] += len(jobs)
            return
        try:
            for job in jobs:
                if deadline and time.monotonic() > deadline:
                    break
                if mailer.sent_today() >= s.daily_send_cap:
                    break
                try:
                    ok, reason = _apply_one(browser, opts, job, autofill, build_cv, cv_for, ask)
                except Exception as exc:   # never let one job stop the rest
                    ok, reason = False, f"Couldn't complete the form automatically ({exc.__class__.__name__})."
                if ok:
                    stats["forms"] += 1
                    out(f"  Applied (form): {job.title} @ {job.company}")
                else:
                    stats["needs_you"] += 1
                    _queue(job, Job.ApplyQueue.REVIEW if reason.startswith("Submitted") else Job.ApplyQueue.FORM,
                           reason)
                    log("apply", f"Left for you: {job.title} @ {job.company}. {reason}", job=job)
        finally:
            browser.close()


def _apply_one(browser, opts, job, autofill, build_cv, cv_for, ask=None):
    """-> (submitted, reason_if_not)"""
    try:
        cv = build_cv(job) if not (job.cv_file and Path(job.cv_file).is_file()) else Path(job.cv_file)
    except Exception:
        cv = cv_for(job)
    if not cv:
        return False, "No CV to upload."
    folder = Path(settings.BASE_DIR) / "cvs" / str(job.pk)
    folder.mkdir(parents=True, exist_ok=True)
    ctx = None
    try:
        ctx = browser.new_context(**opts)
        page = ctx.new_page()
        page.goto(job.apply_url or job.url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        if not autofill.find_form(page):
            return False, "Couldn't find an application form on the page. Use Autofill or apply manually."
        counts = autofill.fill_page(page, job, cv, ask=ask, auto=True)
        if counts["captcha"]:
            return False, "The form has a CAPTCHA, which NexScout never solves. Press Autofill and submit it yourself."
        if counts["missing"]:
            shown = "; ".join(counts["missing"][:3])
            return False, f"Needs your answer: {shown}. Press Autofill to finish it."
        page.screenshot(path=str(folder / "form-filled.png"), full_page=True)
        if not autofill.submit_form(page):
            page.screenshot(path=str(folder / "after-submit.png"), full_page=True)
            return False, ("Submitted, but no confirmation page appeared. Check the listing or your email "
                           "before applying again.")
        proof = folder / "confirmation.png"
        page.screenshot(path=str(proof), full_page=True)
        autofill.mark_submitted(job, by="autopilot", proof=proof)
        return True, ""
    except Exception as exc:
        return False, f"Couldn't complete the form automatically ({exc.__class__.__name__}). Press Autofill to finish it."
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
