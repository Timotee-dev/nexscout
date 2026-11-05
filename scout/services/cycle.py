"""
One full NexScout cycle: collect -> filter -> score -> draft -> (optional) send.
Used by `run_scout` (one cycle) and `autopilot` (repeats on its own).
A lock file stops two cycles overlapping.
"""
import json
import os
import time
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from scout.models import ScoutSettings

from .activity import log
from .collect import fetch_all
from .drafting import draft_pending
from .applier import process_queued
from .auto_apply import run_auto_apply
from .scoring import score_pending

LOCK = Path(settings.BASE_DIR) / ".run_scout.lock"


def _acquire(minutes):
    if LOCK.exists():
        try:
            info = json.loads(LOCK.read_text())
            if time.time() - info.get("started", 0) < (minutes + 15) * 60:
                return False
        except (ValueError, OSError):
            pass
    LOCK.write_text(json.dumps({"pid": os.getpid(), "started": time.time()}))
    return True


def run_cycle(minutes=30, no_ai=False, force_fetch=False, score_limit=None, draft_limit=None,
              send=True, out=print, source="run_scout"):
    """Returns a summary dict, or None if another cycle is already running."""
    if not _acquire(minutes):
        out("Another NexScout cycle is already running. Skipping.")
        return None
    started = time.monotonic()
    deadline = started + minutes * 60
    summary = {"fetched_kept": 0, "scored": 0, "drafted": 0, "sent": 0, "followups": 0}
    try:
        log("run", f"{source} cycle started")
        fetch = fetch_all(force=force_fetch, deadline=deadline)
        summary["fetched_kept"] = sum(r["passed"] for r in fetch.values() if isinstance(r, dict))
        out(f"Fetch: {summary['fetched_kept']} new jobs kept")
        for key, res in fetch.items():
            if not isinstance(res, dict):
                out(f"  {key}: {res}")

        if not no_ai and time.monotonic() < deadline:
            sc = score_pending(limit=score_limit, deadline=deadline)
            summary["scored"] = sc["scored"]
            out(f"Score: {sc['scored']} scored, {sc['failed']} failed, {sc['calls']} Claude calls"
                + (f" ({sc['stopped']})" if sc["stopped"] else ""))
            if sc["stopped"] == "Claude usage limit":
                no_ai = True
        if not no_ai and time.monotonic() < deadline:
            dr = draft_pending(limit=draft_limit, deadline=deadline)
            summary["drafted"] = dr["drafted"]
            out(f"Draft: {dr['drafted']} drafted, {dr['failed']} failed, {dr['calls']} Claude calls"
                + (f" ({dr['stopped']})" if dr["stopped"] else ""))
        if not no_ai and time.monotonic() < deadline:
            q = process_queued()
            if q["drafted"]:
                out(f"Apply queue: {q['drafted']} drafted, {q.get('sent', 0)} sent, "
                    f"{q.get('form', 0)} ready to autofill, {q.get('review', 0)} need review")
        if send and time.monotonic() < deadline:
            try:
                st = run_auto_apply(deadline=deadline, out=out)
            except Exception as exc:   # applying problems must never stop fetching/scoring next time
                log("error", f"Hands-free applying stopped this cycle: {exc}")
                st = {"emailed": 0, "forms": 0, "needs_you": 0, "followups": 0}
            summary["sent"] = st["emailed"] + st["forms"]
            summary["followups"] = st["followups"]
            if summary["sent"] or st["needs_you"] or st["followups"]:
                out(f"Applied: {st['emailed']} by email, {st['forms']} by form, {st['needs_you']} left for you, "
                    f"{st['followups']} follow-ups")

        mins = (time.monotonic() - started) / 60
        text = (f"{summary['fetched_kept']} new · {summary['scored']} scored · {summary['drafted']} drafted"
                + (f" · {summary['sent']} sent" if summary["sent"] else "")
                + (f" · {summary['followups']} follow-ups" if summary["followups"] else ""))
        s = ScoutSettings.get_solo()
        s.autopilot_last_cycle = timezone.now()
        s.autopilot_last_summary = text[:300]
        s.save(update_fields=["autopilot_last_cycle", "autopilot_last_summary", "updated_at"])
        log("run", f"{source} cycle finished in {mins:.1f} min: {text}", **summary)
        out(f"Done in {mins:.1f} min: {text}")
        return summary
    except Exception as exc:
        log("error", f"{source} cycle crashed: {exc}")
        raise
    finally:
        LOCK.unlink(missing_ok=True)
