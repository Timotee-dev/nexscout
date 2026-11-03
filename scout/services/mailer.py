"""
Send email applications from your own Gmail, with your CV attached.

Two ways a send happens:
  * you press "Send application" on a job page (always allowed if set up), or
  * autopilot sends on its own, only when "Send automatically" is on in Settings AND the job
    passes every check in AUTO_SEND_CHECKS below.
Nothing is ever sent twice to the same job, and a daily cap applies to both.
"""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import Q
from django.utils import timezone

from scout.models import Job, Profile, ScoutSettings

from .activity import log

PERSONAL_DOMAINS = ("gmail.", "yahoo.", "hotmail.", "outlook.", "proton", "aol.", "icloud.", "mail.ru", "yandex.")


class SendBlocked(Exception):
    """Why an email wasn't sent, in plain words."""


# ------------------------------------------------------------------ setup checks

def email_ready():
    return bool(settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD)


def cv_path():
    p = Path(settings.CV_PATH)
    return p if p.is_file() else None


def sent_today():
    return Job.objects.filter(email_sent_at__date=timezone.localdate()).count()


# ------------------------------------------------------------------ composing

def default_subject(job):
    if job.apply_subject:
        return job.apply_subject
    return f"Application: {job.title} - {settings.SENDER_NAME}"[:300]


def signature(profile):
    lines = [profile.full_name.split(" ")[0] + " " + profile.full_name.split(" ")[-1] if profile.full_name else ""]
    links = " | ".join(x for x in [profile.portfolio, profile.github] if x)
    if links:
        lines.append(links)
    if profile.phone:
        lines.append(profile.phone)
    return "\n".join(x for x in lines if x)


def compose_body(job, profile=None):
    profile = profile or Profile.get_solo()
    greet = f"Hi {job.company} team," if job.company else "Hello,"
    note = (job.cover_note or "").strip()
    return f"{greet}\n\n{note}\n\nMy CV is attached.\n\nBest regards,\n{signature(profile)}"


def prepare(job, save=True):
    """Fill subject/body from the cover note if you haven't written them yourself."""
    changed = False
    if not job.email_subject:
        job.email_subject = default_subject(job)
        changed = True
    if not job.email_body and job.cover_note:
        job.email_body = compose_body(job)
        changed = True
    if changed and save:
        job.save(update_fields=["email_subject", "email_body", "updated_at"])
    return job


def compose_followup(job, profile=None):
    profile = profile or Profile.get_solo()
    sent = timezone.localtime(job.email_sent_at).strftime("%d %B") if job.email_sent_at else "recently"
    greet = f"Hi {job.company} team," if job.company else "Hello,"
    subject = job.email_subject or default_subject(job)
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    body = (
        f"{greet}\n\n"
        f"I'm following up on my application for the {job.title} role, sent on {sent}. "
        f"I'm still very interested and happy to share more about my work or do a short call "
        f"whenever suits you.\n\n"
        f"Best regards,\n{signature(profile)}"
    )
    return subject[:300], body


# ------------------------------------------------------------------ sending

def _send(to, subject, body, attach_cv=True):
    msg = EmailMessage(subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[to],
                       reply_to=[settings.EMAIL_HOST_USER] if settings.EMAIL_HOST_USER else None)
    if attach_cv:
        msg.attach_file(str(cv_path()), mimetype="application/pdf")
    msg.send(fail_silently=False)


def _base_checks(job):
    if not ScoutSettings.get_solo().send_enabled:
        raise SendBlocked("Sending is switched off in Settings.")
    if not email_ready():
        raise SendBlocked("Gmail isn't set up yet. Add EMAIL_HOST_USER and EMAIL_HOST_PASSWORD to .env.")
    if not job.apply_email:
        raise SendBlocked("This job has no application email address.")


def send_application(job, by="you"):
    _base_checks(job)
    s = ScoutSettings.get_solo()
    if job.email_sent_at:
        raise SendBlocked(f"Already sent on {timezone.localtime(job.email_sent_at):%d %b %H:%M}.")
    if not cv_path():
        raise SendBlocked(f"Your CV wasn't found at {settings.CV_PATH}. Put cv.pdf in the project folder.")
    if sent_today() >= s.daily_send_cap:
        raise SendBlocked(f"Daily limit of {s.daily_send_cap} applications reached. Change it in Settings.")
    prepare(job)
    if not job.email_body.strip():
        raise SendBlocked("The email is empty. Draft a cover note first.")
    try:
        _send(job.apply_email, job.email_subject, job.email_body)
    except Exception as exc:  # SMTP/auth/network
        job.email_error = f"{exc.__class__.__name__}: {exc}"[:300]
        job.save(update_fields=["email_error", "updated_at"])
        log("error", f"Couldn't send application for {job.title} @ {job.company}: {job.email_error}", job=job)
        raise SendBlocked(f"Gmail refused the send: {job.email_error}") from exc
    job.email_sent_at = timezone.now()
    job.email_sent_by = by
    job.email_error = ""
    job.save(update_fields=["email_sent_at", "email_sent_by", "email_error", "updated_at"])
    if job.status in (Job.Status.NEW, Job.Status.SHORTLISTED, Job.Status.SKIPPED):
        job.set_status(Job.Status.APPLIED)
    log("apply", f"Application emailed to {job.apply_email} for {job.title} @ {job.company} (sent by {by})", job=job)
    return job


def send_followup(job, by="you"):
    _base_checks(job)
    if not job.email_sent_at:
        raise SendBlocked("No application email was sent for this job, so there's nothing to follow up.")
    if job.followup_email_sent_at:
        raise SendBlocked("A follow-up was already sent.")
    if job.status != Job.Status.APPLIED:
        raise SendBlocked("Follow-ups are only for jobs still marked Applied.")
    subject, body = compose_followup(job)
    try:
        _send(job.apply_email, subject, body, attach_cv=False)
    except Exception as exc:
        job.email_error = f"{exc.__class__.__name__}: {exc}"[:300]
        job.save(update_fields=["email_error", "updated_at"])
        log("error", f"Couldn't send follow-up for {job.title}: {job.email_error}", job=job)
        raise SendBlocked(f"Gmail refused the send: {job.email_error}") from exc
    now = timezone.now()
    job.followup_email_sent_at = now
    job.followed_up_at = now
    job.follow_up_on = timezone.localdate() + timedelta(days=ScoutSettings.get_solo().followup_days)
    job.save(update_fields=["followup_email_sent_at", "followed_up_at", "follow_up_on", "updated_at"])
    log("apply", f"Follow-up emailed to {job.apply_email} for {job.title} @ {job.company} (sent by {by})", job=job)
    return job


# ------------------------------------------------------------------ autopilot

def auto_send_problems(job, s=None):
    """Every reason autopilot would NOT send this one by itself. Empty list = safe to send."""
    s = s or ScoutSettings.get_solo()
    problems = []
    if not job.apply_email:
        problems.append("no application email")
    if job.email_sent_at:
        problems.append("already sent")
    if job.status not in (Job.Status.NEW, Job.Status.SHORTLISTED):
        problems.append("you've already moved this job")
    if job.score is None or job.score < s.auto_send_min_score:
        problems.append(f"score below {s.auto_send_min_score}")
    if job.open_to_nigeria != Job.NigeriaFit.YES:
        problems.append("not confirmed open to Nigeria")
    if job.scam_flags or job.ai_red_flags:
        problems.append("scam warnings")
    if not job.cover_note or not job.drafted_at:
        problems.append("no draft yet")
    if job.draft_warnings:
        problems.append("draft has warnings to check")
    domain = job.apply_email.partition("@")[2]
    if any(domain.startswith(p) or p in domain for p in PERSONAL_DOMAINS):
        problems.append("goes to a personal email address")
    if job.apply_email and Job.objects.filter(apply_email__iexact=job.apply_email,
                                              email_sent_at__isnull=False).exclude(pk=job.pk).exists():
        problems.append("you already emailed this address")
    return problems


def auto_send_pending():
    s = ScoutSettings.get_solo()
    stats = {"sent": 0, "followups": 0, "blocked": 0}
    if not (s.send_enabled and email_ready()):
        return stats
    if s.auto_send and cv_path():
        qs = (Job.objects.filter(~Q(apply_email=""), email_sent_at__isnull=True,
                                 status__in=[Job.Status.NEW, Job.Status.SHORTLISTED],
                                 score__gte=s.auto_send_min_score)
              .order_by("-score", "-posted_at"))
        for job in qs:
            if sent_today() >= s.daily_send_cap:
                break
            if auto_send_problems(job, s):
                continue
            try:
                send_application(job, by="autopilot")
                stats["sent"] += 1
            except SendBlocked:
                stats["blocked"] += 1
    if s.auto_followup:
        due = Job.objects.filter(status=Job.Status.APPLIED, email_sent_at__isnull=False,
                                 followup_email_sent_at__isnull=True,
                                 follow_up_on__lte=timezone.localdate())
        for job in due:
            try:
                send_followup(job, by="autopilot")
                stats["followups"] += 1
            except SendBlocked:
                stats["blocked"] += 1
    return stats
