"""
Open application forms in a browser and fill them for you. Never clicks Submit.

    python manage.py autofill --queue        # everything in the autofill queue, one after another
    python manage.py autofill 12 15          # specific job ids

For each job: the form opens in a tab, gets filled and highlighted, then NexScout waits.
Submit it yourself (NexScout notices and marks it Applied) or close the tab to skip to the next.
"""
import json
import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

LOCK = Path(settings.BASE_DIR) / ".autofill.lock"


class Command(BaseCommand):
    help = "Fill company application forms in your browser (you click Submit)."

    def add_arguments(self, parser):
        parser.add_argument("ids", nargs="*", type=int)
        parser.add_argument("--queue", action="store_true", help="Process every job in the autofill queue.")
        parser.add_argument("--headless", action="store_true", help="No visible browser (tests only).")
        parser.add_argument("--no-wait", action="store_true", help="Fill and move on without waiting (tests only).")

    def handle(self, *args, **o):
        # Playwright's sync API runs an event loop; Django needs telling that DB calls here are fine.
        os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise CommandError("Playwright isn't installed. Run:  .venv\\Scripts\\pip install playwright")

        from scout.models import Job
        from scout.services import autofill
        from scout.services.cv_builder import build_cv, cv_for

        if o["queue"]:
            jobs = list(Job.objects.filter(apply_queue__in=[Job.ApplyQueue.FORM]).order_by("queued_at"))
        else:
            jobs = list(Job.objects.filter(pk__in=o["ids"]))
        if not jobs:
            self.stdout.write("Nothing to autofill.")
            return

        if LOCK.exists() and time.time() - json.loads(LOCK.read_text() or "{}").get("t", 0) < 3 * 3600 and not o["no_wait"]:
            raise CommandError("Autofill is already running in another window.")
        LOCK.write_text(json.dumps({"t": time.time(), "pid": os.getpid()}))

        try:
            with sync_playwright() as pw:
                ctx = self._open_browser(pw, o["headless"])
                blank = ctx.pages[0] if ctx.pages else None
                for n, job in enumerate(jobs, 1):
                    url = job.apply_url or job.url
                    self.stdout.write(f"\n[{n}/{len(jobs)}] {job.title} @ {job.company}\n  {url}")
                    cv = cv_for(job)
                    if not cv or not job.cv_file:
                        try:
                            cv = build_cv(job)
                        except Exception as exc:
                            self.stdout.write(f"  Couldn't build tailored CV ({exc}); using cv.pdf if present.")
                            cv = cv_for(job)
                    page = ctx.new_page()
                    if blank and not blank.is_closed():
                        blank.close()
                        blank = None
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=12000)
                        except Exception:
                            pass
                        found = autofill.find_form(page)
                        counts = autofill.fill_page(page, job, cv)
                        autofill.mark_filled(job)
                        msg = (f"  Filled {counts['filled']}, drafted {counts['ai']} answers, "
                               f"{counts['attention']} need you." + ("" if found else " (No obvious form found: check the page.)"))
                        self.stdout.write(msg)
                    except Exception as exc:
                        self.stdout.write(self.style.ERROR(f"  Couldn't open or fill this one: {exc}"))
                        continue
                    if o["no_wait"]:
                        continue
                    self.stdout.write("  Waiting: submit it yourself, or close the tab to go to the next one...")
                    submitted = False
                    while not page.is_closed():
                        try:
                            if not submitted and autofill.looks_submitted(page):
                                submitted = True
                                autofill.mark_submitted(job)
                                autofill.banner(page, {}, submitted=True)
                                self.stdout.write(self.style.SUCCESS("  Submitted. Marked Applied in NexScout."))
                            page.wait_for_timeout(1500)
                        except Exception:
                            break
                    if not submitted:
                        self.stdout.write("  Tab closed. If you did submit, press 'I submitted it' in the Apply queue.")
                if not o["no_wait"]:
                    self.stdout.write("\nAll done. You can close the browser.")
                ctx.close()
        finally:
            LOCK.unlink(missing_ok=True)

    def _open_browser(self, pw, headless):
        profile_dir = str(Path(settings.BASE_DIR) / ".browser")
        opts = dict(headless=headless, viewport=None if not headless else {"width": 1280, "height": 900},
                    args=["--start-maximized"] if not headless else [])
        last = None
        for channel in ["chrome", "msedge", None]:   # your Chrome, then Edge (every Windows has it), then Playwright's
            try:
                if channel:
                    return pw.chromium.launch_persistent_context(profile_dir, channel=channel, **opts)
                return pw.chromium.launch_persistent_context(profile_dir, **opts)
            except Exception as exc:
                last = exc
        raise CommandError(f"Couldn't start a browser ({last}). Run:  .venv\\Scripts\\playwright install chromium")
