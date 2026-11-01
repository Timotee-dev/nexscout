import json
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from scout.models import Job, LLMCall, ScoutSettings
from scout.services import claude_client
from scout.services.claude_client import ClaudeError, ClaudeLimitError, parse_cli_output
from scout.services.drafting import find_cliches, parse_draft_reply, review_draft, suggest_project, unsupported_numbers
from scout.services.llm_json import LLMParseError, extract_json
from scout.services.scoring import parse_score_reply, score_pending


class ExtractJsonTests(SimpleTestCase):
    def test_plain(self):
        self.assertEqual(extract_json('[{"a": 1}]'), [{"a": 1}])

    def test_fenced_with_chatter(self):
        self.assertEqual(extract_json('Sure!\n```json\n{"a": 2}\n```\nHope that helps'), {"a": 2})

    def test_embedded(self):
        self.assertEqual(extract_json('Result: [{"id": "J1"}] done'), [{"id": "J1"}])

    def test_garbage(self):
        with self.assertRaises(LLMParseError):
            extract_json("no json here")


class ScoreParserTests(SimpleTestCase):
    def test_happy_path_and_coercion(self):
        reply = json.dumps([
            {"id": "J1", "score": "88.6", "reason": "Strong Django fit.", "matched_skills": ["Django", "DRF"],
             "missing_skills": "Celery, Kubernetes", "seniority": "Mid", "open_to_nigeria": True, "red_flags": []},
            {"id": 2, "score": 140, "reason": "x", "seniority": "wizard", "open_to_nigeria": "maybe"},
        ])
        results, missing = parse_score_reply(reply, ["J1", "J2", "J3"])
        self.assertEqual(results["J1"].score, 89)
        self.assertEqual(results["J1"].missing_skills, ["Celery", "Kubernetes"])
        self.assertEqual(results["J1"].seniority, "mid")
        self.assertEqual(results["J1"].open_to_nigeria, "yes")
        self.assertEqual(results["J2"].score, 100)          # clamped
        self.assertEqual(results["J2"].seniority, "unclear")
        self.assertEqual(results["J2"].open_to_nigeria, "unclear")
        self.assertEqual(missing, ["J3"])

    def test_wrapped_object_and_unknown_ids(self):
        reply = '{"results": [{"id": "J9", "score": 50}, {"id": "J1", "score": 70}]}'
        results, missing = parse_score_reply(reply, ["J1"])
        self.assertEqual(list(results), ["J1"])
        self.assertEqual(missing, [])

    def test_unusable(self):
        with self.assertRaises(LLMParseError):
            parse_score_reply('[{"id": "J1", "score": "high"}]', ["J1"])


class CliOutputTests(SimpleTestCase):
    def test_result_field(self):
        out = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "[1]"})
        self.assertEqual(parse_cli_output(out), "[1]")

    def test_limit_error(self):
        out = json.dumps({"type": "result", "is_error": True, "result": "Claude usage limit reached. Resets 5pm"})
        with self.assertRaises(ClaudeLimitError):
            parse_cli_output(out)

    def test_other_error(self):
        with self.assertRaises(ClaudeError):
            parse_cli_output(json.dumps({"is_error": True, "result": "boom"}))


class DraftHelperTests(SimpleTestCase):
    def test_project_hints(self):
        self.assertEqual(suggest_project("Payments engineer: Stripe webhooks and payouts"), "nexivo")
        self.assertEqual(suggest_project("Multi-tenant SaaS dashboard for organisations"), "nexvote")
        self.assertEqual(suggest_project("Build LLM agents with Claude"), "nexreach")
        self.assertEqual(suggest_project("Marketplace for vendors, orders and catalogue"), "bts")

    def test_cliches_and_numbers(self):
        self.assertIn("passionate", find_cliches("I'm passionate about APIs"))
        self.assertEqual(unsupported_numbers("Hosts get 90–95% and I served 500 users", "split 90–95% automatically"), ["500"])

    def test_review_flags_long_note(self):
        j = Job(title="Dev", description="")
        warnings = review_draft("word " * 160, "hi", "", j)
        self.assertTrue(any("words" in w for w in warnings))

    def test_parse_draft_reply(self):
        reply = '[{"id": "J4", "project": "NexVote", "cover_note": "Note", "opener": "Line one\\nline two"}]'
        out = parse_draft_reply(reply, ["J4"], ["nexivo", "nexvote"])
        self.assertEqual(out["J4"]["project"], "nexvote")
        self.assertEqual(out["J4"]["opener"], "Line one")


class ScorePendingTests(TestCase):
    def setUp(self):
        self.jobs = [Job.objects.create(source="t", external_id=str(i), url="https://x.test/%d" % i,
                                        title=f"Django dev {i}", company="Co", passed_filter=True,
                                        posted_at=timezone.now()) for i in range(3)]

    def fake(self, prompt, purpose, items=1, **kw):
        LLMCall.objects.create(purpose=purpose, model="m", items=items)
        ids = [f"J{j.pk}" for j in self.jobs if f"[J{j.pk}]" in prompt]
        return json.dumps([{"id": i, "score": 72, "reason": "ok", "open_to_nigeria": "yes"} for i in ids])

    def test_batches_and_cap(self):
        s = ScoutSettings.get_solo()
        s.score_batch_size, s.daily_score_cap = 2, 2
        s.save()
        with mock.patch("scout.services.scoring.ask_claude", side_effect=self.fake) as m:
            stats = score_pending()
        self.assertEqual(stats["scored"], 2)          # capped at 2
        self.assertEqual(m.call_count, 1)             # one batch of 2
        with mock.patch("scout.services.scoring.ask_claude", side_effect=self.fake):
            stats = score_pending()
        self.assertEqual(stats["stopped"], "daily scoring cap reached")

    def test_limit_error_stops_run(self):
        with mock.patch("scout.services.scoring.ask_claude", side_effect=ClaudeLimitError("usage limit")):
            stats = score_pending()
        self.assertEqual(stats["stopped"], "Claude usage limit")
        self.assertEqual(Job.objects.filter(scored_at__isnull=False).count(), 0)


class ClaudeClientTests(TestCase):
    @override_settings(CLAUDE_CLI="definitely-not-a-real-binary-xyz")
    def test_missing_cli(self):
        with self.assertRaises(claude_client.ClaudeNotFound):
            claude_client.ask_claude("hi", purpose="ping", model="m")
        self.assertFalse(LLMCall.objects.get().ok)

    def test_api_key_is_stripped(self):
        captured = {}

        def run(cmd, **kw):
            captured.update(kw)
            return mock.Mock(returncode=0, stdout=json.dumps({"result": "ok", "is_error": False}), stderr="")

        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}), \
                mock.patch.object(claude_client, "resolve_cli", return_value="claude"), \
                mock.patch.object(claude_client.subprocess, "run", side_effect=run):
            self.assertEqual(claude_client.ask_claude("hi", purpose="ping", model="m"), "ok")
        self.assertNotIn("ANTHROPIC_API_KEY", captured["env"])
        self.assertEqual(captured["input"], "hi")


class MinScoreTests(TestCase):
    def make(self, i, **kw):
        return Job.objects.create(source="t", external_id=str(i), url=f"https://x.test/{i}", title="Django dev",
                                  company="Co", passed_filter=True, posted_at=timezone.now(), **kw)

    def test_low_scores_move_to_filtered_out(self):
        from scout.services.scoring import ScoreResult, apply_score
        low, ok, mine = self.make(1), self.make(2), self.make(3, status=Job.Status.SHORTLISTED)
        apply_score(low, ScoreResult(score=38))
        apply_score(ok, ScoreResult(score=50))
        apply_score(mine, ScoreResult(score=20))
        low.refresh_from_db(); ok.refresh_from_db(); mine.refresh_from_db()
        self.assertEqual(low.status, Job.Status.SKIPPED)
        self.assertFalse(low.passed_filter)
        self.assertIn("below your minimum (50)", low.filter_reasons[0])
        self.assertEqual(ok.status, Job.Status.NEW)               # 50 is kept
        self.assertEqual(mine.status, Job.Status.SHORTLISTED)     # never overrides your own choice

    def test_refilter_applies_and_undoes_minimum(self):
        from scout.services.collect import refilter_unscored
        j = self.make(4, score=40, scored_at=timezone.now())
        refilter_unscored()
        j.refresh_from_db()
        self.assertEqual(j.status, Job.Status.SKIPPED)
        s = ScoutSettings.get_solo()
        s.min_score = 30
        s.save()
        refilter_unscored()
        j.refresh_from_db()
        self.assertEqual(j.status, Job.Status.NEW)
