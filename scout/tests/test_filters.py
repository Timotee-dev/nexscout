from datetime import datetime, timedelta, timezone as tz
from types import SimpleNamespace

from django.test import SimpleTestCase

from scout.services.filters import (
    FilterConfig, check_location, check_seniority, check_work_authorisation, evaluate, match_keywords,
    required_years,
)
from scout.services.scam import scam_flags

NOW = datetime(2026, 10, 31, 12, tzinfo=tz.utc)
ALLOWED = FilterConfig().allowed_locations


def job(**kw):
    base = dict(title="Python Developer", tags=["python", "django"], description="Django REST work.",
                location="Worldwide", posted_at=NOW - timedelta(days=1))
    base.update(kw)
    return SimpleNamespace(**base)


class LocationTests(SimpleTestCase):
    def test_open_locations_pass(self):
        for loc in ["", "Worldwide", "Anywhere in the World", "Remote", "EMEA", "Africa, Europe",
                    "Nigeria", "Remote - Anywhere", "Lagos, Nigeria", "Americas, EMEA"]:
            self.assertIsNone(check_location(loc, ALLOWED), loc)

    def test_restricted_locations_drop(self):
        cases = {"USA only": "US-only", "United States": "US-only", "Remote (US)": "US-only",
                 "EU only": "EU-only", "Europe": "EU-only", "UK": "UK-only", "United Kingdom": "UK-only"}
        for loc, label in cases.items():
            self.assertIn(label, check_location(loc, ALLOWED) or "", loc)

    def test_unknown_country_dropped(self):
        self.assertIn("Location restricted", check_location("LATAM", ALLOWED))

    def test_south_africa_is_not_africa(self):
        self.assertIsNotNone(check_location("South Africa", ALLOWED))

    def test_timezone_ranges(self):
        self.assertIsNone(check_location("UTC-1 to UTC+3", ALLOWED))
        self.assertIsNotNone(check_location("UTC-8 to UTC-5", ALLOWED))


class AuthorisationTests(SimpleTestCase):
    def test_must_be_authorised(self):
        self.assertIsNotNone(check_work_authorisation("You must be authorized to work in the United States."))
        self.assertIsNotNone(check_work_authorisation("Candidates must be authorised to work in the UK"))
        self.assertIsNotNone(check_work_authorisation("Must be based in Canada."))
        self.assertIsNotNone(check_work_authorisation("US citizens only."))

    def test_harmless_phrasing_passes(self):
        self.assertIsNone(check_work_authorisation("You must be authorized to work in the country you live in."))
        self.assertIsNone(check_work_authorisation("Must be based in Africa or Europe."))
        self.assertIsNone(check_work_authorisation("We hire anywhere. Async team."))


class SeniorityTests(SimpleTestCase):
    def test_years(self):
        self.assertEqual(required_years("5+ years of experience with Python"), 5)
        self.assertEqual(required_years("3-5 years of professional experience"), 3)
        self.assertEqual(required_years("At least seven years experience"), 7)
        self.assertEqual(required_years("minimum of 2 years' backend experience"), 2)
        self.assertIsNone(required_years("Founded 10 years ago, we build tools."))

    def test_senior_titles(self):
        cfg = FilterConfig()
        for t in ["Senior Backend Engineer", "Sr. Python Developer", "Staff Engineer", "Tech Lead (Django)",
                  "Principal Engineer"]:
            self.assertIsNotNone(check_seniority(t, "", cfg), t)
        self.assertIsNone(check_seniority("Backend Engineer", "2+ years experience", cfg))
        self.assertIsNotNone(check_seniority("Backend Engineer", "6+ years of experience", cfg))

    def test_toggle_off(self):
        cfg = FilterConfig(block_senior_titles=False, max_years_required=0)
        self.assertIsNone(check_seniority("Senior Backend Engineer", "8+ years experience", cfg))


class KeywordAndEvaluateTests(SimpleTestCase):
    def test_keywords(self):
        self.assertEqual(match_keywords("Full-stack Engineer", ["python"], "", ["full-stack Python"]), ["full-stack Python"])
        self.assertEqual(match_keywords("Back-end developer", [], "", ["backend"]), ["backend"])
        self.assertEqual(match_keywords("Content Marketer", [], "Write blogs", ["Django", "Python"]), [])
        self.assertEqual(match_keywords("Pythonista wanted", [], "", ["Python"]), [])  # word boundary

    def test_evaluate_pass(self):
        r = evaluate(job(), FilterConfig(), now=NOW)
        self.assertTrue(r.passed, r.reasons)

    def test_evaluate_old_job_dropped(self):
        r = evaluate(job(posted_at=NOW - timedelta(days=20)), FilterConfig(), now=NOW)
        self.assertFalse(r.passed)
        self.assertTrue(any("Older than" in x for x in r.reasons))

    def test_evaluate_no_keyword(self):
        r = evaluate(job(title="Designer", tags=[], description="Figma"), FilterConfig(), now=NOW)
        self.assertFalse(r.keyword_match)


class ScamTests(SimpleTestCase):
    def test_flags(self):
        flags = scam_flags("", "Python Dev", "Pay a small fee for training. DM us on Telegram. Paid in USDT.")
        self.assertEqual(len(flags), 4)
        self.assertEqual(scam_flags("Acme", "Python Dev", "Normal job, apply via our site."), [])
        self.assertTrue(scam_flags("Acme", "Dev", "Apply to hr.acme@gmail.com"))
        self.assertTrue(scam_flags("Acme", "Dev", "", salary_max=400_000))


class RelevanceTests(SimpleTestCase):
    def test_python_only_in_description_is_not_enough(self):
        self.assertEqual(match_keywords("Risk Analyst", [], "Python is a plus.", ["Python", "backend"]), [])
        self.assertEqual(match_keywords("Software Engineer", ["java"], "Some Python scripting.", ["Python"]), [])

    def test_specific_framework_in_description_counts(self):
        self.assertEqual(match_keywords("Software Engineer", [], "Our stack is Django and Postgres.", ["Django"]), ["Django"])

    def test_title_and_tags_count(self):
        self.assertEqual(match_keywords("Senior Python Engineer", [], "", ["Python"]), ["Python"])
        self.assertEqual(match_keywords("Engineer", ["python"], "", ["Python"]), ["Python"])

    def test_excluded_titles(self):
        r = evaluate(job(title="Python Data Analyst"), FilterConfig(), now=NOW)
        self.assertFalse(r.passed)
        self.assertTrue(any("Not a backend dev role" in x for x in r.reasons))
        self.assertTrue(evaluate(job(title="Backend Engineer (Python)"), FilterConfig(), now=NOW).passed)
