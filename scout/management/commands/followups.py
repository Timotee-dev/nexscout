from django.core.management.base import BaseCommand
from django.utils import timezone

from scout.models import Job


class Command(BaseCommand):
    help = "List applications whose follow-up date has arrived."

    def add_arguments(self, parser):
        parser.add_argument("--upcoming", type=int, default=0, help="Also show follow-ups due in the next N days.")

    def handle(self, *args, **o):
        today = timezone.localdate()
        until = today + timezone.timedelta(days=o["upcoming"])
        jobs = Job.objects.filter(status=Job.Status.APPLIED, follow_up_on__lte=until).order_by("follow_up_on")
        if not jobs:
            self.stdout.write(self.style.SUCCESS("No follow-ups due." if not o["upcoming"] else "Nothing due in that window."))
            return
        self.stdout.write(f"{'Due':<12}{'Applied':<12}Role")
        for j in jobs:
            late = (today - j.follow_up_on).days
            tag = "TODAY" if late == 0 else (f"{late}d late" if late > 0 else f"in {-late}d")
            applied = j.applied_at.strftime("%d %b") if j.applied_at else "-"
            line = f"{tag:<12}{applied:<12}{j.title} @ {j.company}"
            self.stdout.write(self.style.WARNING(line) if late >= 0 else line)
            self.stdout.write(f"{'':<24}{j.listing_url}")
        self.stdout.write('\nAfter you follow up, press "I followed up" on the job page to push the next reminder out.')
