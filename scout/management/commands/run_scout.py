"""
Full cycle with a time limit: fetch + filter → score → draft. Safe to schedule.
A lock file stops two runs overlapping (e.g. Task Scheduler firing while you run it by hand).
"""
import json
import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from scout.services.activity import log
from scout.services.collect import fetch_all
from scout.services.drafting import draft_pending
from scout.services.scoring import score_pending

LOCK = Path(settings.BASE_DIR) / ".run_scout.lock"


class Command(BaseCommand):
    help = "Run a full NexScout cycle (collect → filter → score → draft) within a time limit."

    def add_arguments(self, parser):
        parser.add_argument("--minutes", type=float, default=30, help="Stop starting new work after this long.")
        parser.add_argument("--score-limit", type=int, default=None, help="Max jobs to score this run.")
        parser.add_argument("--draft-limit", type=int, default=None, help="Max drafts this run.")
        parser.add_argument("--no-ai", action="store_true", help="Only fetch + filter (no Claude calls).")
        parser.add_argument("--force-fetch", action="store_true", help="Ignore source fetch intervals.")

    def _acquire(self, minutes):
        if LOCK.exists():
            try:
                info = json.loads(LOCK.read_text())
                if time.time() - info.get("started", 0) < (minutes + 15) * 60:
                    return False
            except (ValueError, OSError):
                pass
        LOCK.write_text(json.dumps({"pid": os.getpid(), "started": time.time()}))
        return True

    def handle(self, *args, **o):
        if not self._acquire(o["minutes"]):
            self.stdout.write(self.style.WARNING("Another run_scout is in progress (lock file present). Exiting."))
            return
        started = time.monotonic()
        deadline = started + o["minutes"] * 60
        summary = {}
        try:
            log("run", f"run_scout started ({o['minutes']:g} min limit)")
            fetch = fetch_all(force=o["force_fetch"], deadline=deadline)
            kept = sum(r["passed"] for r in fetch.values() if isinstance(r, dict))
            summary["fetched_kept"] = kept
            self.stdout.write(f"Fetch: {kept} new jobs kept")
            for key, res in fetch.items():
                if not isinstance(res, dict):
                    self.stdout.write(f"  {key}: {res}")

            if not o["no_ai"]:
                if time.monotonic() < deadline:
                    sc = score_pending(limit=o["score_limit"], deadline=deadline)
                    summary["scored"] = sc["scored"]
                    self.stdout.write(f"Score: {sc['scored']} scored, {sc['failed']} failed, {sc['calls']} Claude calls"
                                      + (f" ({sc['stopped']})" if sc["stopped"] else ""))
                    if sc["stopped"] == "Claude usage limit":
                        o["no_ai"] = True
                if not o["no_ai"] and time.monotonic() < deadline:
                    dr = draft_pending(limit=o["draft_limit"], deadline=deadline)
                    summary["drafted"] = dr["drafted"]
                    self.stdout.write(f"Draft: {dr['drafted']} drafted, {dr['failed']} failed, {dr['calls']} Claude calls"
                                      + (f" ({dr['stopped']})" if dr["stopped"] else ""))
            mins = (time.monotonic() - started) / 60
            log("run", f"run_scout finished in {mins:.1f} min: {summary}", **summary)
            self.stdout.write(self.style.SUCCESS(f"Done in {mins:.1f} min. Nothing was sent or submitted; review drafts in the dashboard."))
        except Exception as exc:
            log("error", f"run_scout crashed: {exc}")
            raise
        finally:
            LOCK.unlink(missing_ok=True)
