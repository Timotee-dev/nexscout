"""One full cycle with a time limit: fetch + filter -> score -> draft -> (optional) send."""
from django.core.management.base import BaseCommand

from scout.services.cycle import run_cycle


class Command(BaseCommand):
    help = "Run one NexScout cycle (collect, filter, score, draft, and auto-send if enabled)."

    def add_arguments(self, parser):
        parser.add_argument("--minutes", type=float, default=30, help="Stop starting new work after this long.")
        parser.add_argument("--score-limit", type=int, default=None, help="Max jobs to score this run.")
        parser.add_argument("--draft-limit", type=int, default=None, help="Max drafts this run.")
        parser.add_argument("--no-ai", action="store_true", help="Only fetch + filter (no Claude calls).")
        parser.add_argument("--no-send", action="store_true", help="Never send emails in this run.")
        parser.add_argument("--force-fetch", action="store_true", help="Ignore source fetch intervals.")

    def handle(self, *args, **o):
        run_cycle(minutes=o["minutes"], no_ai=o["no_ai"], force_fetch=o["force_fetch"],
                  score_limit=o["score_limit"], draft_limit=o["draft_limit"], send=not o["no_send"],
                  out=self.stdout.write)
