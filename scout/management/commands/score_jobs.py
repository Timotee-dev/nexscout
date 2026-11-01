from django.core.management.base import BaseCommand

from scout.services.scoring import score_pending


class Command(BaseCommand):
    help = "Score filtered jobs against your profile with Claude (batched, respects the daily cap)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Max jobs to score this run.")
        parser.add_argument("--retry-failed", action="store_true", help="Also retry jobs whose last scoring failed.")

    def handle(self, *args, **opts):
        stats = score_pending(limit=opts["limit"], retry_failed=opts["retry_failed"])
        self.stdout.write(self.style.SUCCESS(
            f"Scored {stats['scored']} · failed {stats['failed']} · Claude calls {stats['calls']}"))
        if stats["stopped"]:
            self.stdout.write(f"Note: {stats['stopped']}")
