"""
Keeps NexScout working on its own. Leave it running (scripts/start_nexscout.bat does this
for you, and install_startup.bat starts it every time you log in to Windows).

Every minute it checks in; when a cycle is due (Settings -> Autopilot -> Run every) it runs one.
Each job board still keeps its own polite gap, and Claude still respects your daily caps.
Close the window (or press Ctrl+C) to stop it.
"""
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from scout.models import ScoutSettings
from scout.services.activity import log
from scout.services.cycle import run_cycle

TICK_SECONDS = 60


class Command(BaseCommand):
    help = "Run NexScout on its own: a full cycle every N minutes (set in Settings)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run a single check (used by tests).")

    def tick(self):
        close_old_connections()
        s = ScoutSettings.get_solo()
        now = timezone.now()
        s.autopilot_last_beat = now
        s.save(update_fields=["autopilot_last_beat", "updated_at"])
        if not s.autopilot_enabled:
            return "paused"
        due = (s.autopilot_last_cycle is None
               or (now - s.autopilot_last_cycle).total_seconds() >= s.autopilot_every_minutes * 60)
        if not due:
            return "waiting"
        stamp = timezone.localtime().strftime("%d %b %H:%M")
        self.stdout.write(f"\n[{stamp}] Running a cycle...")
        try:
            run_cycle(minutes=max(10, min(45, s.autopilot_every_minutes - 5)), out=self.stdout.write,
                      source="autopilot")
        except Exception as exc:  # keep autopilot alive; the error is in the Activity log
            self.stdout.write(self.style.ERROR(f"Cycle failed: {exc}"))
            # don't retry immediately in a tight loop
            s = ScoutSettings.get_solo()
            s.autopilot_last_cycle = timezone.now()
            s.save(update_fields=["autopilot_last_cycle", "updated_at"])
        return "ran"

    def handle(self, *args, **o):
        if o["once"]:
            self.tick()
            return
        log("run", "Autopilot started")
        self.stdout.write(self.style.SUCCESS(
            "NexScout autopilot is running. Leave this window open (minimised is fine). Ctrl+C to stop."))
        try:
            while True:
                self.tick()
                time.sleep(TICK_SECONDS)
        except KeyboardInterrupt:
            log("run", "Autopilot stopped")
            self.stdout.write("Autopilot stopped.")
