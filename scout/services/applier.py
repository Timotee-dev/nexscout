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
from .claude_client import ClaudeError, ClaudeLimitError
from .llm_json import LLMParseError

DONE = (Job.Status.APPLIED, Job.Status.INTERVIEWING, Job.Status.OFFER, Job.Status.REJECTED, Job.Status.GHOSTED)


def _queue(job, state, error=""):
    job.apply_queue = state
    job.apply_error = error[:300]
    job.queued_at = job.queued_at or timezone.now()
    if job.status in (Job.Status.NEW, Job.Status.SKIPPED):
        job.status = Job.Status.SHORTLISTED
        job.status_changed_at = timezone.now()
    job.save(update_fields=["apply_queue", "apply_error", "queued_at", "status", "status_changed_at", "updated_at"])


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
            _queue(job, Job.ApplyQueue.REVIEW, "The draft has something to check: " + "; ".join(job.draft_warnings))
            return "review", "The draft has something to check first. It's waiting in Needs review."
        if not (s.send_enabled and mailer.email_ready()):
            _queue(job, Job.ApplyQueue.REVIEW, "Gmail isn't set up yet. Copy the email below and send it yourself, or add Gmail to .env.")
            return "review", "Gmail isn't set up yet, so it's waiting in Needs review. Open it to copy the email."
        try:
            mailer.send_application(job, by=by)
        except mailer.SendBlocked as exc:
            _queue(job, Job.ApplyQueue.REVIEW, f"Not sent: {exc}")
            return "review", f"Not sent: {exc} It's waiting in Needs review."
        job.apply_queue, job.apply_error = "", ""
        job.save(update_fields=["apply_queue", "apply_error", "updated_at"])
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
        except ClaudeLimitError as exc:
            # Pro usage limit: keep them queued; autopilot retries on a later cycle.
            for job in batch:
                job.apply_error = "Claude's usage limit was reached. NexScout will try again automatically later."
                job.save(update_fields=["apply_error", "updated_at"])
            log("error", f"Claude usage limit while drafting queued applications: {exc}")
            stats["failed"] += len(batch)
            break
        except Exception as exc:  # anything else: never leave a job silently stuck
            reason = _plain_reason(exc)
            for job in batch:
                _queue(job, Job.ApplyQueue.REVIEW, f"Couldn't write the cover note: {reason}")
                log("error", f"Couldn't draft application for {job.title} @ {job.company}: {reason}", job=job)
            stats["failed"] += len(batch)
            continue
        done_ids = {j.pk for j in done}
        for job in batch:
            if job.pk not in done_ids:
                _queue(job, Job.ApplyQueue.REVIEW, "Claude didn't return a draft for this job. Press Try again.")
                log("error", f"No draft returned for {job.title} @ {job.company}", job=job)
                stats["failed"] += 1
        for job in done:
            stats["drafted"] += 1
            try:
                kind, _ = route(job, by="you")
            except Exception as exc:
                _queue(job, Job.ApplyQueue.REVIEW, f"Drafted, but couldn't finish: {_plain_reason(exc)}")
                log("error", f"Couldn't finish applying to {job.title}: {exc}", job=job)
                kind = "review"
            stats[kind] = stats.get(kind, 0) + 1
    return stats


def _plain_reason(exc):
    text = str(exc) or exc.__class__.__name__
    if "locked" in text.lower():
        return "the database was busy. Press Try again."
    if "not found on PATH" in text or "Claude Code CLI" in text:
        return "Claude Code wasn't found. Check CLAUDE_CLI in .env."
    return text[:200]


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
                    break  # e.g. Claude limit: autopilot retries later
        except Exception as exc:  # last resort: record it so the page can show it
            log("error", f"Background apply worker stopped: {exc}")
        finally:
            close_old_connections()
            _worker_lock.release()
    threading.Thread(target=run, daemon=True).start()
