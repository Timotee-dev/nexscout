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
    """Applications sent today, by email or company form, by you or autopilot (one daily limit for both)."""
    today = timezone.localdate()
    return Job.objects.filter(Q(email_sent_at__date=today) | Q(form_submitted_at__date=today)).count()


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

def _send(to, subject, body, attachment=None, filename=None):
    msg = EmailMessage(subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[to],
                       reply_to=[settings.EMAIL_HOST_USER] if settings.EMAIL_HOST_USER else None)
    if attachment:
        msg.attach(filename or Path(attachment).name, Path(attachment).read_bytes(), "application/pdf")
    msg.send(fail_silently=False)


def attachment_for(job):
    """(path, filename) of the CV to attach: the job's tailored CV if possible, else cv.pdf."""
    from .cv_builder import build_cv, cv_filename, cv_for
    s = ScoutSettings.get_solo()
    if s.tailored_cv and not (job.cv_file and Path(job.cv_file).is_file()):
        try:
            build_cv(job)
        except Exception as exc:
            log("error", f"Tailored CV failed for {job.title}, using cv.pdf instead: {exc}", job=job)
    path = cv_for(job)
    if not path:
        return None, None
    return path, cv_filename(job)


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
    if sent_today() >= s.daily_send_cap:
        raise SendBlocked(f"Daily limit of {s.daily_send_cap} applications reached. Change it in Settings.")
    prepare(job)
    if not job.email_body.strip():
        raise SendBlocked("The email is empty. Draft a cover note first.")
    cv_file, cv_name = attachment_for(job)
    if not cv_file:
        raise SendBlocked(f"No CV to attach. Put cv.pdf in the project folder (or turn on tailored CVs).")
    try:
        _send(job.apply_email, job.email_subject, job.email_body, cv_file, cv_name)
    except Exception as exc:  # SMTP/auth/network
        job.email_error = f"{exc.__class__.__name__}: {exc}"[:300]
        job.save(update_fields=["email_error", "updated_at"])
        log("error", f"Couldn't send application for {job.title} @ {job.company}: {job.email_error}", job=job)
        raise SendBlocked(f"Gmail refused the send: {job.email_error}") from exc
    job.email_sent_at = timezone.now()
    job.email_sent_by = by
    job.email_error = ""
    job.applied_via, job.applied_by = "email", by
    job.save(update_fields=["email_sent_at", "email_sent_by", "email_error", "applied_via", "applied_by", "updated_at"])
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
        _send(job.apply_email, subject, body)
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
    """Kept for compatibility: see auto_apply.auto_apply_problems."""
    from .auto_apply import auto_apply_problems
    return auto_apply_problems(job, s)


def auto_send_pending():
    """Kept for compatibility: hands-free applying now lives in auto_apply.run_auto_apply."""
    from .auto_apply import run_auto_apply
    st = run_auto_apply()
    return {"sent": st["emailed"] + st["forms"], "followups": st["followups"], "blocked": st["needs_you"]}
