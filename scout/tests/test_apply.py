import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.core import mail
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from scout.models import Job, ScoutSettings
from scout.services import mailer
from scout.services.apply_email import detect, find_apply_email, find_apply_subject


class DetectTests(SimpleTestCase):
    def test_fluence_style(self):
        text = ("To apply to this job please send an email directly to techjobs@fluencetraining.com "
                "with the email title [Full-Stack Developer @ Fluence International, Inc.]")
        self.assertEqual(detect("", text),
                         ("techjobs@fluencetraining.com", "[Full-Stack Developer @ Fluence International, Inc.]"))

    def test_mailto_and_quotes(self):
        html = '<p>Send your CV to <a href="mailto:Jobs@Acme.io">us</a>. Subject: "Backend Engineer"</p>'
        self.assertEqual(find_apply_email(html, ""), "jobs@acme.io")
        self.assertEqual(find_apply_subject(html, ""), "Backend Engineer")

    def test_ignores_unrelated_addresses(self):
        self.assertEqual(find_apply_email("", "Questions about privacy? privacy@acme.com"), "")
        self.assertEqual(find_apply_email("", "Our office is great. ceo@acme.com wrote this blog."), "")
        self.assertEqual(find_apply_email("", "Apply on our careers page."), "")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   EMAIL_HOST_USER="me@gmail.com", EMAIL_HOST_PASSWORD="app-pass",
                   DEFAULT_FROM_EMAIL="Timothy Arifalo <me@gmail.com>")
class SendTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cv = Path(self.tmp) / "cv.pdf"
        self.cv.write_bytes(b"%PDF-1.4 test")
        self.override = override_settings(CV_PATH=str(self.cv))
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp)

    def job(self, i=1, **kw):
        base = dict(source="t", external_id=str(i), url=f"https://x.test/{i}", title="Django Developer",
                    company="Acme", passed_filter=True, apply_email=f"jobs{i}@acme.io", score=82,
                    open_to_nigeria="yes", cover_note="Your payments API is close to what I built in Nexivo.",
                    drafted_at=timezone.now(), posted_at=timezone.now())
        base.update(kw)
        return Job.objects.create(**base)

    def test_send_attaches_cv_and_marks_applied(self):
        j = mailer.send_application(self.job(), by="you")
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ["jobs1@acme.io"])
        self.assertEqual(m.attachments[0][0], "Timothy-Arifalo-CV-acme.pdf")   # tailored CV
        self.assertTrue(m.attachments[0][1].startswith(b"%PDF"))
        self.assertIn("Hi Acme team,", m.body)
        self.assertIn("Nexivo", m.body)
        j.refresh_from_db()
        self.assertEqual(j.status, Job.Status.APPLIED)
        self.assertEqual(j.follow_up_on, timezone.localdate() + timedelta(days=7))
        self.assertIsNotNone(j.email_sent_at)

    def test_never_sends_twice_and_respects_cap(self):
        j = self.job()
        mailer.send_application(j)
        with self.assertRaises(mailer.SendBlocked):
            mailer.send_application(j)
        s = ScoutSettings.get_solo()
        s.daily_send_cap = 1
        s.save()
        with self.assertRaises(mailer.SendBlocked):
            mailer.send_application(self.job(2))
        self.assertEqual(len(mail.outbox), 1)

    def test_required_subject_used(self):
        j = mailer.send_application(self.job(apply_subject="[Backend @ Acme]"))
        self.assertEqual(mail.outbox[0].subject, "[Backend @ Acme]")
        self.assertEqual(j.email_subject, "[Backend @ Acme]")

    def test_missing_cv_blocks(self):
        s = ScoutSettings.get_solo()
        s.tailored_cv = False
        s.save()
        self.cv.unlink()
        with self.assertRaises(mailer.SendBlocked):
            mailer.send_application(self.job())

    @override_settings(EMAIL_HOST_PASSWORD="")
    def test_not_configured_blocks(self):
        with self.assertRaises(mailer.SendBlocked):
            mailer.send_application(self.job())

    def test_auto_send_only_safe_jobs(self):
        s = ScoutSettings.get_solo()
        s.auto_send = True
        s.save()
        good = self.job(1)
        self.job(2, score=70)                                     # below auto-send score
        self.job(3, open_to_nigeria="unclear")
        self.job(4, scam_flags=["Asks you to pay"])
        self.job(5, apply_email="someone@gmail.com")              # personal address
        self.job(6, draft_warnings=["Numbers not in your CV: 500"])
        self.job(7, status=Job.Status.SKIPPED)                    # you skipped it
        stats = mailer.auto_send_pending()
        self.assertEqual(stats["sent"], 1)
        self.assertEqual([m.to for m in mail.outbox], [["jobs1@acme.io"]])
        good.refresh_from_db()
        self.assertEqual(good.email_sent_by, "autopilot")

    def test_auto_send_off_by_default(self):
        self.job()
        self.assertEqual(mailer.auto_send_pending()["sent"], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_followup(self):
        j = mailer.send_application(self.job())
        j.follow_up_on = timezone.localdate()
        j.save()
        s = ScoutSettings.get_solo()
        s.auto_followup = True
        s.save()
        self.assertEqual(mailer.auto_send_pending()["followups"], 1)
        self.assertTrue(mail.outbox[1].subject.startswith("Re: "))
        self.assertEqual(mail.outbox[1].attachments, [])
        self.assertEqual(mailer.auto_send_pending()["followups"], 0)  # only once

    def test_send_button_view(self):
        j = self.job()
        r = self.client.get(f"/jobs/{j.pk}/")
        self.assertContains(r, "Send application")
        self.client.post(f"/jobs/{j.pk}/action/", {"action": "send_application"})
        self.assertEqual(len(mail.outbox), 1)


class AutopilotTests(TestCase):
    def test_tick_runs_cycle_when_due_then_waits(self):
        from unittest import mock
        with mock.patch("scout.management.commands.autopilot.run_cycle") as rc:
            call_command("autopilot", "--once")
            self.assertEqual(rc.call_count, 1)
        s = ScoutSettings.get_solo()
        s.autopilot_last_cycle = timezone.now()
        s.save()
        with mock.patch("scout.management.commands.autopilot.run_cycle") as rc:
            call_command("autopilot", "--once")
            rc.assert_not_called()
        self.assertIsNotNone(ScoutSettings.get_solo().autopilot_last_beat)
        r = self.client.get("/")
        self.assertContains(r, "Autopilot on")

    def test_paused(self):
        from unittest import mock
        s = ScoutSettings.get_solo()
        s.autopilot_enabled = False
        s.save()
        with mock.patch("scout.management.commands.autopilot.run_cycle") as rc:
            call_command("autopilot", "--once")
            rc.assert_not_called()
