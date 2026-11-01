from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from scout.models import Job, OutreachTarget, Profile


class StatusTests(TestCase):
    def setUp(self):
        self.job = Job.objects.create(source="t", external_id="1", url="https://x.test/1", title="Django dev",
                                      company="Co", passed_filter=True)

    def test_applied_sets_followup(self):
        self.job.set_status(Job.Status.APPLIED)
        self.assertEqual(self.job.follow_up_on, timezone.localdate() + timedelta(days=7))
        self.assertIsNotNone(self.job.applied_at)

    def test_moving_on_clears_followup(self):
        self.job.set_status(Job.Status.APPLIED)
        self.job.set_status(Job.Status.INTERVIEWING)
        self.assertIsNone(self.job.follow_up_on)
        self.assertIsNotNone(self.job.applied_at)

    def test_followup_due(self):
        self.job.set_status(Job.Status.APPLIED)
        self.job.follow_up_on = timezone.localdate()
        self.assertTrue(self.job.is_followup_due)

    def test_status_view(self):
        r = self.client.post(reverse("scout:job_status", args=[self.job.pk]), {"status": "applied"})
        self.assertEqual(r.status_code, 302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "applied")


class PagesTests(TestCase):
    def test_all_pages_render(self):
        job = Job.objects.create(source="remoteok", external_id="1", url="https://x.test/1", title="Django dev",
                                 company="Co", passed_filter=True, score=80, cover_note="Hi", opener="Hey",
                                 drafted_at=timezone.now())
        t = OutreachTarget.objects.create(company_name="Startup")
        for name, args in [("overview", []), ("jobs", []), ("job_detail", [job.pk]), ("pipeline", []),
                           ("outreach", []), ("outreach_detail", [t.pk]), ("settings", []), ("activity", [])]:
            r = self.client.get(reverse(f"scout:{name}", args=args))
            self.assertEqual(r.status_code, 200, name)

    def test_profile_seeded_from_cv(self):
        p = Profile.get_solo()
        self.assertEqual(p.full_name, "Timothy Moyinoluwa Arifalo")
        self.assertIn("Nexivo", p.prompt_profile)
        self.assertIn("Paystack", p.prompt_profile)

    def test_followups_command(self):
        call_command("followups")
