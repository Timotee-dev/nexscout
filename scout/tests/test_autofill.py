import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock, skipUnless

from django.core.management.base import CommandError
from django.test import TestCase, TransactionTestCase, override_settings

from scout.models import Job, ScoutSettings
from scout.services import autofill

FORMS = Path(__file__).parent / "fixtures" / "forms"

try:
    import playwright  # noqa: F401
    HAVE_PW = True
except ImportError:
    HAVE_PW = False


class ClassifyTests(TestCase):
    def f(self, label, type="text", tag="input", **kw):
        return dict(label=label, type=type, tag=tag, name=kw.get("name", ""), id="", placeholder="", required=False)

    def test_standard_fields(self):
        self.assertEqual(autofill.classify(self.f("First Name *"))[0], "first_name")
        self.assertEqual(autofill.classify(self.f("Full name"))[0], "full_name")
        self.assertEqual(autofill.classify(self.f("Company name"))[0], None)
        self.assertEqual(autofill.classify(self.f("Resume/CV", type="file"))[0], "resume")
        self.assertEqual(autofill.classify(self.f("Cover letter", type="file"))[0], None)
        self.assertEqual(autofill.classify(self.f("Additional information", type="textarea", tag="textarea"))[0], "cover_letter")

    def test_sensitive_left_for_you(self):
        for q in ["Are you legally authorized to work in the US?", "Do you require visa sponsorship?",
                  "What are your salary expectations?", "Gender", "I agree to the privacy policy"]:
            self.assertTrue(autofill.classify(self.f(q))[1], q)


@skipUnless(HAVE_PW, "playwright not installed")
class BrowserFillTests(TestCase):
    """Runs a real headless browser against sample Greenhouse- and Lever-style forms."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        from unittest import SkipTest
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = None
        for channel in (None, "chrome", "msedge"):
            try:
                cls.browser = cls.pw.chromium.launch(channel=channel) if channel else cls.pw.chromium.launch()
                break
            except Exception:
                continue
        if cls.browser is None:
            cls.pw.stop()
            raise SkipTest("No browser available for Playwright")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "browser", None):
            cls.browser.close()
            cls.pw.stop()
        super().tearDownClass()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cv = Path(self.tmp) / "cv.pdf"
        self.cv.write_bytes(b"%PDF-1.4 test")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def job(self, page):
        return Job.objects.create(source="careers", external_id=page, url=(FORMS / page).as_uri(),
                                  title="Backend Engineer", company="Acme", passed_filter=True,
                                  cover_note="I built and run Nexivo, a ticketing platform used at two institutions.")

    def fake_ask(self, prompt, purpose, items=1, **kw):
        self.prompts.append(prompt)
        return '{"answers": [{"n": 1, "answer": "When SMTP was blocked on Nexivo I moved email to Brevo\'s HTTP API."}]}'

    def test_greenhouse_style_form(self):
        self.prompts = []
        job = self.job("greenhouse.html")
        page = self.browser.new_page()
        page.goto(job.url)
        self.assertTrue(autofill.find_form(page))
        counts = autofill.fill_page(page, job, self.cv, ask=self.fake_ask)
        v = lambda sel: page.input_value(sel)
        self.assertEqual(v("#first_name"), "Timothy")
        self.assertEqual(v("#last_name"), "Arifalo")
        self.assertEqual(v("#email"), "arifalotimothy@gmail.com")
        self.assertIn("github.com/Timotee-dev", v("#q2"))
        self.assertIn("netlify", v("#q3"))
        self.assertEqual(v("#q1"), "")                       # no LinkedIn on your CV: left for you
        self.assertEqual(v("#q4"), "")                       # work authorisation: never answered for you
        self.assertEqual(v("#q5"), "")                       # salary: never answered for you
        self.assertIn("Brevo", v("#q6"))                     # drafted from your profile
        self.assertTrue(page.eval_on_selector("#resume", "e => e.files.length === 1"))
        self.assertEqual(page.get_attribute("#q4", "data-nexscout-state"), "attention")
        self.assertEqual(len(self.prompts), 1)
        self.assertGreaterEqual(counts["filled"], 6)
        self.assertGreaterEqual(counts["attention"], 2)
        self.assertIn("never submits", page.inner_text("#nexscout-banner"))
        # Nothing was submitted by NexScout
        self.assertTrue(page.url.endswith("greenhouse.html"))
        self.assertFalse(autofill.looks_submitted(page))
        # You submit -> detected -> Applied
        page.check('input[name="consent"]')
        page.fill("#q4", "Yes")
        page.click("#submit")
        page.wait_for_load_state()
        self.assertTrue(autofill.looks_submitted(page))
        autofill.mark_submitted(job)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.APPLIED)
        page.close()

    def test_lever_style_two_step(self):
        self.prompts = []
        job = self.job("lever_job.html")
        page = self.browser.new_page()
        page.goto(job.url)
        self.assertTrue(autofill.find_form(page))           # follows "Apply for this job"
        self.assertTrue(page.url.endswith("lever_apply.html"))
        autofill.fill_page(page, job, self.cv, ask=self.fake_ask)
        self.assertEqual(page.input_value('input[name="name"]'), "Timothy Arifalo")
        self.assertEqual(page.input_value('input[name="org"]'), "Independent (Nexora)")
        self.assertIn("Nexivo", page.input_value('textarea[name="comments"]'))   # cover note
        self.assertEqual(page.input_value('select[name="sponsor"]'), "")         # sponsorship left for you
        self.assertTrue(page.eval_on_selector('input[name="resume"]', "e => e.files.length === 1"))
        page.close()



@skipUnless(HAVE_PW, "playwright not installed")
class AutofillCommandTests(TransactionTestCase):
    def setUp(self):
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        self.tmp = tempfile.mkdtemp()
        self.cv = Path(self.tmp) / "cv.pdf"
        self.cv.write_bytes(b"%PDF-1.4 test")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def job(self, page):
        return Job.objects.create(source="careers", external_id=page, url=(FORMS / page).as_uri(),
                                  title="Backend Engineer", company="Acme", passed_filter=True,
                                  cover_note="I built and run Nexivo.")

    def test_command_runs_queue(self):
        from django.core.management import call_command
        job = self.job("greenhouse.html")
        job.apply_queue = Job.ApplyQueue.FORM
        job.save()
        with mock.patch("scout.services.autofill.answer_questions", return_value=["An answer."]), \
                override_settings(CV_PATH=str(self.cv)):
            from io import StringIO
            out = StringIO()
            try:
                call_command("autofill", "--queue", "--headless", "--no-wait", stdout=out)
            except CommandError as exc:
                if "browser" in str(exc).lower():
                    self.skipTest("No browser available for Playwright")
                raise
            self.output = out.getvalue()
        job.refresh_from_db()
        self.assertEqual(job.apply_queue, Job.ApplyQueue.FILLED, self.output)
        self.assertIsNotNone(job.autofilled_at)
