"""
"Apply" as one action (swipe right, or the Apply button):
  * Email jobs  -> draft if needed -> tailored CV -> send from your Gmail.
  * Form jobs   -> draft if needed -> tailored CV -> autofill queue (browser fills, you click Submit).
A draft with warnings is held for your review instead of being sent.
"""
import threading

from django.db import close_old_connections
from django.utils import timezone

from scout.models import Job, Profile, ScoutSettings

from .activity import log
from .claude_client import ClaudeError
from .llm_json import LLMParseError

DONE = (Job.Status.APPLIED, Job.Status.INTERVIEWING, Job.Status.OFFER, Job.Status.REJECTED, Job.Status.GHOSTED)


def _queue(job, state):
    job.apply_queue = state
    job.queued_at = job.queued_at or timezone.now()
    if job.status in (Job.Status.NEW, Job.Status.SKIPPED):
        job.status = Job.Status.SHORTLISTED
        job.status_changed_at = timezone.now()
    job.save(update_fields=["apply_queue", "queued_at", "status", "status_changed_at", "updated_at"])


def route(job, by="you"):
    """Job has a draft: send it, hold it for review, or queue it for autofill. Returns (kind, message)."""
    from . import mailer
    s = ScoutSettings.get_solo()
    if s.tailored_cv and not job.cv_file:
        try:
            from .cv_builder import build_cv
            build_cv(job)
        except Exception as exc:
            log("error", f"Tailored CV failed for {job.title}: {exc}", job=job)
    if job.apply_email:
        if job.draft_warnings:
            _queue(job, Job.ApplyQueue.REVIEW)
            return "review", "The draft has something to check first. It's waiting in Needs review."
        if not (s.send_enabled and mailer.email_ready()):
            _queue(job, Job.ApplyQueue.REVIEW)
            return "review", "Gmail isn't set up yet, so it's waiting in Needs review. Open it to copy the email."
        try:
            mailer.send_application(job, by=by)
        except mailer.SendBlocked as exc:
            _queue(job, Job.ApplyQueue.REVIEW)
            return "review", f"Not sent: {exc} It's waiting in Needs review."
        job.apply_queue = ""
        job.save(update_fields=["apply_queue", "updated_at"])
        return "sent", f"Sent to {job.apply_email} with your CV."
    _queue(job, Job.ApplyQueue.FORM)
    return "form", "Added to your autofill queue. Open Autofill to fill the form, then click Submit yourself."


def apply_now(job, by="you"):
    if job.status in DONE or job.email_sent_at:
        return "done", "You've already applied to this one."
    if not job.cover_note:
        _queue(job, Job.ApplyQueue.QUEUED)
        return "queued", "Writing your cover note now. It'll be sent or queued in a minute."
    return route(job, by=by)


def process_queued(limit=None):
    """Draft every queued job (in small batches) and route it. Used by autopilot and the background worker."""
    from .drafting import _draft_batch
    s = ScoutSettings.get_solo()
    jobs = list(Job.objects.filter(apply_queue=Job.ApplyQueue.QUEUED).order_by("queued_at")[: limit or 20])
    stats = {"drafted": 0, "sent": 0, "form": 0, "review": 0, "failed": 0}
    if not jobs:
        return stats
    profile = Profile.get_solo()
    for i in range(0, len(jobs), max(1, s.draft_batch_size)):
        batch = jobs[i:i + max(1, s.draft_batch_size)]
        try:
            done = _draft_batch(batch, profile, s)
        except (ClaudeError, LLMParseError) as exc:
            log("error", f"Couldn't draft queued applications: {exc}")
            stats["failed"] += len(batch)
            continue
        for job in done:
            stats["drafted"] += 1
            kind, _ = route(job, by="you")
            stats[kind] = stats.get(kind, 0) + 1
    return stats


_worker_lock = threading.Lock()


def process_in_background():
    """Swipe-right on a job without a draft: draft and route it without making you wait."""
    def run():
        if not _worker_lock.acquire(blocking=False):
            return  # one worker at a time; it picks up everything queued
        close_old_connections()
        try:
            while Job.objects.filter(apply_queue=Job.ApplyQueue.QUEUED).exists():
                before = Job.objects.filter(apply_queue=Job.ApplyQueue.QUEUED).count()
                process_queued(limit=5)
                if Job.objects.filter(apply_queue=Job.ApplyQueue.QUEUED).count() >= before:
                    break  # drafting failed (e.g. Claude limit); autopilot will retry later
        finally:
            close_old_connections()
            _worker_lock.release()
    threading.Thread(target=run, daemon=True).start()
