from django.core.management.base import BaseCommand

from scout.services.drafting import draft_pending


class Command(BaseCommand):
    help = "Draft cover notes + openers for jobs at or above your score threshold. Never sends anything."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Max jobs to draft this run.")

    def handle(self, *args, **opts):
        stats = draft_pending(limit=opts["limit"])
        self.stdout.write(self.style.SUCCESS(
            f"Drafted {stats['drafted']} · failed {stats['failed']} · Claude calls {stats['calls']}"))
        if stats["stopped"]:
            self.stdout.write(f"Note: {stats['stopped']}")
