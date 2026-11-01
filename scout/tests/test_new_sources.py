import json
from datetime import datetime, timezone as tz
from pathlib import Path

from django.test import SimpleTestCase

from scout.services.filters import FilterConfig, evaluate
from scout.services.sources.hackernews import HackerNewsHiringSource
from scout.services.sources.himalayas import HimalayasSource
from scout.services.sources.jobicy import JobicySource

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 1, 18, tzinfo=tz.utc)


class JobicyTests(SimpleTestCase):
    def test_parse(self):
        jobs = JobicySource(session=object()).parse(json.loads((FIX / "jobicy.json").read_text()))
        self.assertEqual(len(jobs), 2)
        j = jobs[0]
        self.assertEqual((j.title, j.company, j.location), ("Python Backend Developer", "Lumen Pay", "EMEA"))
        self.assertEqual(j.salary, "$40,000–$60,000")
        self.assertEqual(j.salary_max, 60000)
        self.assertIn("Midweight", j.job_type)
        self.assertIsNotNone(j.posted_at)
        self.assertEqual(jobs[1].salary, "$30–$45 / hourly")
        self.assertIsNone(jobs[1].salary_max)   # hourly pay isn't treated as a yearly figure
        self.assertTrue(evaluate(j, FilterConfig(), now=NOW).passed)


class HimalayasTests(SimpleTestCase):
    def test_parse(self):
        jobs = HimalayasSource(session=object()).parse(json.loads((FIX / "himalayas.json").read_text()))
        self.assertEqual(jobs[0].location, "Nigeria, Kenya")
        self.assertEqual(jobs[1].location, "Worldwide")
        self.assertTrue(jobs[0].url.startswith("https://himalayas.app/"))
        self.assertEqual(jobs[0].apply_url, "https://northwind.example/jobs/1")
        self.assertIsNotNone(jobs[0].posted_at.tzinfo)
        self.assertTrue(evaluate(jobs[0], FilterConfig(max_age_days=3650), now=NOW).passed)


class HackerNewsTests(SimpleTestCase):
    def test_parse_thread(self):
        jobs = HackerNewsHiringSource(session=object()).parse_thread(json.loads((FIX / "hn_thread.json").read_text()))
        self.assertEqual(len(jobs), 2)
        acme = jobs[0]
        self.assertEqual(acme.company, "Acme Payments")
        self.assertEqual(acme.title, "Backend Engineer (Python/Django)")
        self.assertEqual(acme.location, "REMOTE (Worldwide)")
        self.assertEqual(acme.job_type, "Full-time")
        self.assertEqual(acme.url, "https://news.ycombinator.com/item?id=45000001")
        self.assertTrue(evaluate(acme, FilterConfig(), now=NOW).passed)
        self.assertFalse(evaluate(jobs[1], FilterConfig(), now=NOW).passed)
