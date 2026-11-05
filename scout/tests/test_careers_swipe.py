import json
from unittest import mock

from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from scout.models import Job, ScoutSettings
from scout.services.ats import detect_ats
from scout.services.collect import ingest
from scout.services.filters import FilterConfig
from scout.services.sources.careers import CareerSitesSource

GH = {"jobs": [{"id": 101, "title": "Software Engineer, Python", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/101",
                "location": {"name": "Remote - EMEA"}, "updated_at": "2026-11-03T10:00:00-04:00",
                "content": "&lt;p&gt;Build Django services.&lt;/p&gt;", "departments": [{"name": "Engineering"}]},
               {"id": 102, "title": "Account Executive", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/102",
                "location": {"name": "New York"}, "updated_at": "2026-11-03T10:00:00Z", "content": ""}]}
LEVER = [{"id": "abc", "text": "Backend Developer (Django)", "hostedUrl": "https://jobs.lever.co/beta/abc",
          "applyUrl": "https://jobs.lever.co/beta/abc/apply", "categories": {"location": "Africa", "commitment": "Contract",
          "team": "Engineering"}, "workplaceType": "remote", "createdAt": 1762160000000,
          "descriptionPlain": "Django and DRF.", "lists": [{"text": "Requirements", "content": "<li>Python</li>"}]}]
ASHBY = {"jobs": [{"id": "z1", "title": "Python Engineer", "location": "Lagos", "isRemote": True,
                   "secondaryLocations": [{"location": "Nairobi"}], "publishedAt": "2026-11-02T09:00:00.000+00:00",
                   "jobUrl": "https://jobs.ashbyhq.com/gamma/z1", "applyUrl": "https://jobs.ashbyhq.com/gamma/z1/application",
                   "descriptionPlain": "APIs in Python.", "employmentType": "FullTime",
                   "compensation": {"compensationTierSummary": "$40K – $60K"}},
                  {"id": "z2", "title": "Hidden", "isListed": False}]}


class CareerParseTests(SimpleTestCase):
    def setUp(self):
        self.src = CareerSitesSource(session=object(), companies=[])

    def test_greenhouse(self):
        jobs = self.src.parse_greenhouse(GH, "acme")
        self.assertEqual(jobs[0].location, "Remote - EMEA")
        self.assertEqual(jobs[0].description, "Build Django services.")
        self.assertEqual(jobs[0].external_id, "gh:acme:101")
        self.assertIsNotNone(jobs[0].posted_at)

    def test_lever(self):
        j = self.src.parse_lever(LEVER, "beta")[0]
        self.assertEqual(j.location, "Remote - Africa")
        self.assertEqual(j.apply_url, "https://jobs.lever.co/beta/abc/apply")
        self.assertIn("Requirements", j.description)
        self.assertEqual(j.posted_at.year, 2025)  # 1762160000000 ms

    def test_ashby(self):
        jobs = self.src.parse_ashby(ASHBY, "gamma")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "Remote - Lagos, Nairobi")
        self.assertIn("40K", jobs[0].salary)

    def test_missing_boards_reported(self):
        src = CareerSitesSource(session=object(), companies=[("greenhouse", "nope")])
        from scout.services.sources.base import SourceError
        with mock.patch.object(src, "fetch_greenhouse", side_effect=SourceError("x: HTTP 404")):
            with self.assertRaises(SourceError):
                src.fetch([])
        self.assertIn("greenhouse:nope", src.problems[0])

    def test_detect_ats(self):
        self.assertEqual(detect_ats("https://boards.greenhouse.io/gitlab/jobs/1"), ("greenhouse", "gitlab"))
        self.assertEqual(detect_ats("https://jobs.lever.co/binance/x"), ("lever", "binance"))
        self.assertEqual(detect_ats("", "https://jobs.ashbyhq.com/posthog/1/application"), ("ashby", "posthog"))
        self.assertEqual(detect_ats("https://example.com/careers"), ("", ""))


class DiscoveryTests(TestCase):
    def test_ingest_reports_new_companies_and_ats(self):
        from scout.services.sources.base import RawJob
        raw = RawJob(source="remoteok", external_id="1", title="Python Developer", company="Delta",
                     url="https://remoteok.com/1", apply_url="https://jobs.lever.co/delta/123",
                     description="Django", location="Worldwide", posted_at=timezone.now())
        stats = ingest([raw], FilterConfig())
        self.assertEqual(stats["discovered"], [("lever", "delta")])
        self.assertEqual(Job.objects.get().ats, "lever")
        s = ScoutSettings.get_solo()
        self.assertTrue(s.add_career_company("lever", "delta"))
        self.assertFalse(s.add_career_company("lever", "delta"))
        self.assertIn(("lever", "delta"), s.career_company_list)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   EMAIL_HOST_USER="me@gmail.com", EMAIL_HOST_PASSWORD="x", DEFAULT_FROM_EMAIL="T <me@gmail.com>")
class SwipeTests(TestCase):
    def job(self, i, **kw):
        base = dict(source="t", external_id=str(i), url=f"https://x.test/{i}", title="Django Developer", company="Acme",
                    passed_filter=True, score=80, scored_at=timezone.now(), open_to_nigeria="yes",
                    posted_at=timezone.now())
        base.update(kw)
        return Job.objects.create(**base)

    def post(self, j, decision):
        return self.client.post(f"/swipe/{j.pk}/", {"decision": decision}, HTTP_X_REQUESTED_WITH="fetch").json()

    def test_deck_shows_best_open_matches(self):
        a, b = self.job(1, score=90), self.job(2, score=60)
        self.job(3, open_to_nigeria="no")
        self.job(4, score=None, scored_at=None)
        r = self.client.get("/swipe/")
        self.assertEqual([j.pk for j in r.context["deck"]], [a.pk, b.pk])

    def test_skip_and_save(self):
        a, b = self.job(1), self.job(2)
        self.post(a, "skip")
        self.post(b, "save")
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.status, b.status), (Job.Status.SKIPPED, Job.Status.SHORTLISTED))

    def test_apply_email_job_sends_with_tailored_cv(self):
        j = self.job(1, apply_email="jobs@acme.io", cover_note="Your payments API is close to Nexivo.",
                     drafted_at=timezone.now())
        data = self.post(j, "apply")
        self.assertEqual(data["kind"], "sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(mail.outbox[0].attachments[0][0].endswith(".pdf"))
        j.refresh_from_db()
        self.assertEqual(j.status, Job.Status.APPLIED)

    def test_apply_form_job_goes_to_autofill_queue(self):
        j = self.job(1, cover_note="Note.", drafted_at=timezone.now(), ats="greenhouse")
        self.assertEqual(self.post(j, "apply")["kind"], "form")
        j.refresh_from_db()
        self.assertEqual(j.apply_queue, Job.ApplyQueue.FORM)
        self.assertTrue(j.cv_file.endswith(".pdf"))
        self.assertContains(self.client.get("/apply/"), "Autofill 1 form")

    def test_apply_with_draft_warnings_is_held(self):
        j = self.job(1, apply_email="jobs@acme.io", cover_note="I served 500 merchants.", drafted_at=timezone.now(),
                     draft_warnings=["Numbers not in your CV: 500"])
        self.assertEqual(self.post(j, "apply")["kind"], "review")
        self.assertEqual(len(mail.outbox), 0)

    def test_apply_without_draft_is_drafted_then_routed(self):
        from scout.services import applier
        j = self.job(1, apply_email="jobs@acme.io")
        with mock.patch.object(applier, "process_in_background"):
            self.assertEqual(self.post(j, "apply")["kind"], "queued")
        reply = json.dumps([{"id": f"J{j.pk}", "project": "nexivo", "cover_note": "Payments APIs like Nexivo's.",
                             "opener": "Hi", "cv_summary": "Backend engineer focused on Django and payments.",
                             "project_order": ["nexivo", "nexvote"]}])
        with mock.patch("scout.services.drafting.ask_claude", return_value=reply):
            stats = applier.process_queued()
        self.assertEqual(stats["sent"], 1)
        j.refresh_from_db()
        self.assertEqual(j.cv_summary, "Backend engineer focused on Django and payments.")
        self.assertEqual(j.cv_project_order, ["nexivo", "nexvote"])

    def test_cv_download(self):
        j = self.job(1, matched_skills=["Django"])
        r = self.client.get(f"/jobs/{j.pk}/cv/")
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(b"".join(r.streaming_content).startswith(b"%PDF"))


class SwipeFallbackAndAssetsTests(TestCase):
    def test_buttons_work_without_javascript(self):
        j = Job.objects.create(source="t", external_id="nojs", url="https://x.test/nojs", title="Django Dev", company="Acme",
                               passed_filter=True, score=80, scored_at=timezone.now(), open_to_nigeria="yes")
        r = self.client.get("/swipe/")
        self.assertContains(r, f'action="/swipe/{j.pk}/"')
        r = self.client.post(f"/swipe/{j.pk}/", {"decision": "skip"})       # plain form post, no JS header
        self.assertRedirects(r, "/swipe/")
        j.refresh_from_db()
        self.assertEqual(j.status, Job.Status.SKIPPED)

    def test_scripts_and_styles_are_versioned(self):
        r = self.client.get("/swipe/")
        self.assertRegex(r.content.decode(), r"scout/js/app\.js\?v=\d+")
        self.assertRegex(r.content.decode(), r"scout/js/swipe\.js\?v=\d+")
        self.assertRegex(r.content.decode(), r"scout/css/app\.css\?v=\d+")
