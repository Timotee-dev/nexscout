import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import SkipTest, skipUnless

from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from scout.models import Job, ScoutSettings
from scout.services import answers
from scout.services.auto_apply import auto_apply_problems, run_auto_apply

FORMS = Path(__file__).parent / "fixtures" / "forms"
try:
    import playwright  # noqa: F401
    HAVE_PW = True
except ImportError:
    HAVE_PW = False


class AnswerSheetTests(TransactionTestCase):
    def test_classify_and_choose(self):
        self.assertEqual(answers.classify("Are you legally authorized to work in the United States?"), "authorized")
        self.assertEqual(answers.classify("Will you require visa sponsorship?"), "sponsorship")
        self.assertEqual(answers.classify("Gender"), "demographic")
        self.assertEqual(answers.classify("Date of birth"), "never")
        self.assertEqual(answers.choose_option("Are you authorized to work in the US?", ["Yes", "No"]), "No")
        self.assertEqual(answers.choose_option("Are you authorized to work in Nigeria?", ["Yes", "No"]), "Yes")
        self.assertEqual(answers.choose_option("Require sponsorship?", ["Yes", "No"]), "No")
        self.assertEqual(answers.choose_option("Gender", ["Male", "Female", "I don't wish to answer"]), "I don't wish to answer")
        self.assertIsNone(answers.choose_option("Race", ["White", "Black"]))           # no decline option: left for you
        self.assertEqual(answers.choose_option("Years of experience", ["0-1 years", "1-3 years", "5+ years"]), "1-3 years")
        self.assertEqual(answers.choose_option("Country of residence", ["Kenya", "Nigeria"]), "Nigeria")
        self.assertIsNone(answers.choose_option("Date of birth", ["1990", "2000"]))
        self.assertIsNone(answers.text_answer("What is your expected salary in USD per year?", kind="number"))  # blank number

    def test_problems(self):
        s = ScoutSettings.get_solo()
        j = Job.objects.create(source="t", external_id="p1", url="https://x/1", title="Dev", company="Acme",
                               passed_filter=True, score=80, cover_note="Note", drafted_at=timezone.now())
        self.assertIn("answer sheet not confirmed in Settings", auto_apply_problems(j, s))
        s.answers_confirmed = True
        s.save()
        self.assertEqual(auto_apply_problems(j, s), [])
        j.scam_flags = ["Asks you to pay"]
        self.assertIn("scam warnings", auto_apply_problems(j, s))


def fake_ask(prompt, purpose, items=1, **kw):
    if '"picks"' in prompt:   # multiple-choice questions it can't justify: leave empty
        return json.dumps({"picks": [{"n": i, "option": ""} for i in range(1, items + 1)]})
    return json.dumps({"answers": [{"n": i, "answer": "Delta's payments work matches what I built in Nexivo."}
                                   for i in range(1, items + 1)]})


@skipUnless(HAVE_PW, "playwright not installed")
class HandsFreeFormTests(TransactionTestCase):
    """Real headless browser: fills and submits sample forms end to end."""

    def setUp(self):
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(BASE_DIR=Path(self.tmp), CV_PATH=str(Path(self.tmp) / "cv.pdf"))
        self.override.enable()
        s = ScoutSettings.get_solo()
        s.answers_confirmed = True
        s.auto_send = True
        s.save()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp)

    def job(self, page, i=1, **kw):
        base = dict(source="careers", external_id=f"{page}{i}", url=(FORMS / page).as_uri(), title="Python Engineer",
                    company=f"Delta {page} {i}", passed_filter=True, score=82, open_to_nigeria="yes",
                    cover_note="Your payments platform is close to what I built in Nexivo.", drafted_at=timezone.now(),
                    posted_at=timezone.now())
        base.update(kw)
        return Job.objects.create(**base)

    def browser(self, pw):
        for channel in (None, "chrome", "msedge"):
            try:
                return (pw.chromium.launch(channel=channel) if channel else pw.chromium.launch()), {"viewport": {"width": 1200, "height": 900}}
            except Exception:
                continue
        raise SkipTest("No browser available")

    def run_it(self):
        return run_auto_apply(out=lambda *_: None, browser_factory=self.browser, ask=fake_ask)

    def test_full_form_is_filled_and_submitted(self):
        j = self.job("full_auto.html")
        stats = self.run_it()
        j.refresh_from_db()
        self.assertEqual(stats["forms"], 1, j.apply_error)
        self.assertEqual(j.status, Job.Status.APPLIED)
        self.assertEqual((j.applied_via, j.applied_by), ("form", "autopilot"))
        self.assertTrue(Path(j.apply_proof).is_file())
        self.assertIsNotNone(j.form_submitted_at)
        self.assertEqual(j.follow_up_on, timezone.localdate() + timezone.timedelta(days=7))

    def test_captcha_is_left_for_you(self):
        j = self.job("captcha.html")
        stats = self.run_it()
        j.refresh_from_db()
        self.assertEqual(stats["forms"], 0)
        self.assertIn("CAPTCHA", j.apply_error)
        self.assertEqual(j.apply_queue, Job.ApplyQueue.FORM)
        self.assertNotEqual(j.status, Job.Status.APPLIED)

    def test_unanswerable_required_question_is_left_for_you(self):
        j = self.job("unknown_required.html")
        self.run_it()
        j.refresh_from_db()
        self.assertIn("Needs your answer", j.apply_error)
        self.assertIn("office", j.apply_error.lower())
        self.assertIsNone(j.form_submitted_at)

    def test_no_confirmation_is_flagged_not_retried(self):
        j = self.job("noconfirm.html")
        self.run_it()
        j.refresh_from_db()
        self.assertEqual(j.apply_queue, Job.ApplyQueue.REVIEW)
        self.assertIn("no confirmation", j.apply_error)
        self.assertIn("waiting for you in the Apply queue", auto_apply_problems(j))   # never re-submitted

    def test_daily_limit(self):
        s = ScoutSettings.get_solo()
        s.daily_send_cap = 1
        s.save()
        a, b = self.job("full_auto.html", 1), self.job("full_auto.html", 2)
        stats = self.run_it()
        self.assertEqual(stats["forms"], 1)
        self.assertEqual(Job.objects.filter(form_submitted_at__isnull=False).count(), 1)

    def test_not_confirmed_means_nothing_submitted(self):
        s = ScoutSettings.get_solo()
        s.answers_confirmed = False
        s.save()
        j = self.job("full_auto.html")
        stats = self.run_it()
        j.refresh_from_db()
        self.assertEqual(stats["forms"], 0)
        self.assertIsNone(j.form_submitted_at)
