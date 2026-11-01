from django.core.management.base import BaseCommand

from scout.services.collect import fetch_all, refilter_unscored
from scout.services.sources import get_sources


class Command(BaseCommand):
    help = "Collect new jobs from enabled sources and apply the rule-based filters (no Claude usage)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Ignore each source's minimum interval.")
        parser.add_argument("--source", action="append", choices=list(get_sources()), help="Only this source (repeatable).")
        parser.add_argument("--refilter", action="store_true", help="Re-apply filters to unscored jobs and exit.")

    def handle(self, *args, **opts):
        if opts["refilter"]:
            n = refilter_unscored()
            self.stdout.write(self.style.SUCCESS(f"Re-filtered. {n} jobs changed."))
            return
        results = fetch_all(force=opts["force"], only=opts["source"])
        for key, res in results.items():
            label = get_sources()[key].label
            if isinstance(res, dict):
                self.stdout.write(self.style.SUCCESS(
                    f"{label:18} kept {res['passed']:>3} · filtered {res['rejected']:>3} · "
                    f"dupes {res['duplicates']:>3} · seen {res['seen']:>3} · off-topic {res['irrelevant']:>3}"))
            elif str(res).startswith("error"):
                self.stdout.write(self.style.ERROR(f"{label:18} {res}"))
            else:
                self.stdout.write(f"{label:18} {res}")
