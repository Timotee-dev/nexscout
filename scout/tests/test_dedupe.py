import json
from datetime import timedelta
from pathlib import Path

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from scout.models import Job
from scout.services.collect import ingest
from scout.services.dedupe import company_key, is_duplicate, title_key
from scout.services.filters import FilterConfig
from scout.services.sources.remoteok import RemoteOKSource
from scout.services.sources.remotive import RemotiveSource
from scout.services.sources.weworkremotely import WeWorkRemotelySource

FIX = Path(__file__).parent / "fixtures"


class KeyTests(SimpleTestCase):
    def test_company_key(self):
        self.assertEqual(company_key("Acme Pay, Inc."), company_key("ACME PAY LLC"))
        self.assertEqual(company_key("Smith & Co Ltd"), "smith and")

    def test_title_key(self):
        self.assertEqual(title_key("Back-end Developer (Remote)"), title_key("Backend Engineer"))
        self.assertEqual(title_key("Full Stack Python Developer"), title_key("Full-stack Python Engineer"))

    def test_is_duplicate(self):
        self.assertTrue(is_duplicate("Acme Pay Inc.", "Python / Django Developer (Remote)",
                                     "Acme Pay", "Python/Django Developer"))
        self.assertFalse(is_duplicate("Acme", "Backend Engineer", "Other Co", "Backend Engineer"))
        self.assertFalse(is_duplicate("Acme", "Backend Engineer", "Acme", "Frontend Engineer"))


class SourceParseTests(SimpleTestCase):
    def test_remoteok_skips_legal_notice(self):
        jobs = RemoteOKSource(session=object()).parse(json.loads((FIX / "remoteok.json").read_text()))
        self.assertEqual(len(jobs), 3)
        self.assertEqual(jobs[0].salary, "$40,000–$70,000")
        self.assertIn("payments", jobs[0].description)
        self.assertNotIn("<p>", jobs[0].description)

    def test_remotive(self):
        jobs = RemotiveSource(session=object()).parse(json.loads((FIX / "remotive.json").read_text()))
        self.assertEqual(jobs[1].location, "EMEA")
        self.assertIsNotNone(jobs[0].posted_at.tzinfo)

    def test_wwr_splits_company_and_title(self):
        jobs = WeWorkRemotelySource(session=object()).parse((FIX / "wwr.rss").read_bytes())
        self.assertEqual(jobs[0].company, "Nimbus Labs")
        self.assertEqual(jobs[0].title, "Junior Backend Developer (Python)")
        self.assertEqual(jobs[0].location, "Anywhere in the World")


class IngestTests(TestCase):
    def raws(self):
        ro = RemoteOKSource(session=object()).parse(json.loads((FIX / "remoteok.json").read_text()))
        rm = RemotiveSource(session=object()).parse(json.loads((FIX / "remotive.json").read_text()))
        return ro + rm

    def test_cross_source_duplicate_is_merged(self):
        now = timezone.now()
        raws = self.raws()
        for r in raws:
            r.posted_at = now - timedelta(days=1)
        stats = ingest(raws, FilterConfig(), now=now)
        self.assertEqual(stats["duplicates"], 1)
        self.assertEqual(stats["irrelevant"], 1)  # the marketing job isn't stored
        acme = Job.objects.get(company="Acme Pay")
        self.assertEqual(acme.also_seen_on[0]["source"], "remotive")
        # Re-running is idempotent
        again = ingest(raws, FilterConfig(), now=now)
        self.assertEqual(again["passed"] + again["rejected"], 0)

    def test_filtered_jobs_are_kept_as_skipped(self):
        ingest(self.raws(), FilterConfig(max_age_days=3650))
        senior = Job.objects.get(company="BigCo")
        self.assertFalse(senior.passed_filter)
        self.assertEqual(senior.status, Job.Status.SKIPPED)
        self.assertTrue(senior.filter_reasons)
        flagged = Job.objects.get(company="Wavelength")
        self.assertTrue(flagged.scam_flags)
