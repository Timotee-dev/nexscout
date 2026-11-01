"""
SCORE stage. Batches several filtered jobs into one Claude call to keep Pro usage light.
"""
import time
from dataclasses import dataclass, field

from django.utils import timezone

from scout.models import Job, Profile, ScoutSettings

from .activity import log
from .claude_client import ClaudeError, ClaudeLimitError, ask_claude
from .filters import below_min_score_reason
from .llm_json import LLMParseError, as_list_of_dicts, extract_json
from .text import truncate

DESC_CHARS = 1400
SENIORITY = {"intern", "junior", "mid", "senior", "lead", "unclear"}

SCORE_PROMPT = """You are screening remote job listings for one candidate. Be strict and honest.
Do not use any tools. Reply with JSON only — no prose, no code fences.

CANDIDATE PROFILE
{profile}

SCORING GUIDE (fit_score 0–100)
- 85–100: core stack matches (Python/Django/DRF backend), level is junior/mid or contract, clearly open to Nigeria/worldwide.
- 65–84: good overlap, a gap or two the candidate could close quickly.
- 40–64: partial overlap (different main stack, or seniority stretch).
- 0–39: wrong stack, wrong level, or not open to someone in Nigeria.
Cap the score at 40 if the role is not genuinely open to someone living in Nigeria.
Only list matched skills that actually appear in the candidate profile.

JOBS
{jobs}

Return a JSON array with exactly one object per job, in this shape:
[{{"id": "J123", "score": 0-100, "reason": "one plain sentence, max 25 words",
   "matched_skills": ["..."], "missing_skills": ["..."],
   "seniority": "intern|junior|mid|senior|lead|unclear",
   "open_to_nigeria": "yes|no|unclear",
   "red_flags": ["scam or legitimacy concerns, empty list if none"]}}]
"""


@dataclass
class ScoreResult:
    score: int
    reason: str = ""
    matched_skills: list = field(default_factory=list)
    missing_skills: list = field(default_factory=list)
    seniority: str = "unclear"
    open_to_nigeria: str = "unclear"
    red_flags: list = field(default_factory=list)


def job_ref(job):
    return f"J{job.pk}"


def format_job_for_prompt(job):
    return "\n".join([
        f"[{job_ref(job)}] {job.title} — {job.company or 'Unknown company'}",
        f"Location: {job.location or 'not stated'} | Type: {job.job_type or 'n/a'} | Salary: {job.salary or 'not listed'}",
        f"Tags: {', '.join(job.tags[:12]) if job.tags else '-'}",
        f"Description: {truncate(job.description, DESC_CHARS)}",
    ])


def build_score_prompt(profile_text, jobs):
    return SCORE_PROMPT.format(profile=profile_text.strip(),
                               jobs="\n\n---\n\n".join(format_job_for_prompt(j) for j in jobs))


def _str_list(value, limit=12):
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",")]
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        s = str(v).strip()
        if s and s.lower() not in {"none", "n/a"}:
            out.append(s[:60])
    return out[:limit]


def _yes_no(value):
    if isinstance(value, bool):
        return "yes" if value else "no"
    v = str(value or "").strip().lower()
    if v in {"yes", "y", "true", "open"}:
        return "yes"
    if v in {"no", "n", "false", "closed"}:
        return "no"
    return "unclear"


def parse_score_reply(text, expected_ids):
    """-> (results: {id: ScoreResult}, missing: [id]). Raises LLMParseError if unusable."""
    items = as_list_of_dicts(extract_json(text), "results")
    wanted = {str(i).upper() for i in expected_ids}
    results = {}
    for item in items:
        ref = str(item.get("id") or item.get("job_id") or "").strip().upper()
        if ref and not ref.startswith("J"):
            ref = f"J{ref}"
        if ref not in wanted:
            continue
        raw_score = item.get("score", item.get("fit_score"))
        try:
            score = int(round(float(raw_score)))
        except (TypeError, ValueError):
            continue
        seniority = str(item.get("seniority") or item.get("seniority_guess") or "unclear").strip().lower()
        results[ref] = ScoreResult(
            score=max(0, min(100, score)),
            reason=str(item.get("reason") or "").strip()[:300],
            matched_skills=_str_list(item.get("matched_skills")),
            missing_skills=_str_list(item.get("missing_skills")),
            seniority=seniority if seniority in SENIORITY else "unclear",
            open_to_nigeria=_yes_no(item.get("open_to_nigeria")),
            red_flags=_str_list(item.get("red_flags"), limit=5),
        )
    if not results and items:
        raise LLMParseError("Reply had no usable scores")
    missing = sorted(wanted - set(results))
    return results, missing


def apply_score(job, r):
    job.score = r.score
    job.score_reason = r.reason
    job.matched_skills = r.matched_skills
    job.missing_skills = r.missing_skills
    job.seniority_guess = r.seniority
    job.open_to_nigeria = r.open_to_nigeria
    job.ai_red_flags = r.red_flags
    job.scored_at = timezone.now()
    job.score_error = ""
    # Below your minimum score: move it out of the way (only if you haven't acted on it yet).
    if job.status == Job.Status.NEW:
        reason = below_min_score_reason(r.score, ScoutSettings.get_solo().min_score)
        if reason:
            job.status = Job.Status.SKIPPED
            job.passed_filter = False
            job.filter_reasons = [reason]
    job.save()


def scored_today():
    return Job.objects.filter(scored_at__date=timezone.localdate()).count()


def pending_jobs():
    return (Job.objects.filter(passed_filter=True, scored_at__isnull=True, score_error="",
                               status__in=[Job.Status.NEW, Job.Status.SHORTLISTED])
            .order_by("-posted_at", "-fetched_at"))


def score_pending(limit=None, deadline=None, retry_failed=False):
    s = ScoutSettings.get_solo()
    remaining = max(0, s.daily_score_cap - scored_today())
    stats = {"scored": 0, "failed": 0, "calls": 0, "cap_left": remaining, "stopped": ""}
    if remaining == 0:
        stats["stopped"] = "daily scoring cap reached"
        return stats
    n = min(limit, remaining) if limit else remaining
    qs = pending_jobs()
    if retry_failed:
        qs = (Job.objects.filter(passed_filter=True, scored_at__isnull=True,
                                 status__in=[Job.Status.NEW, Job.Status.SHORTLISTED])
              .order_by("-posted_at"))
    jobs = list(qs[:n])
    if not jobs:
        stats["stopped"] = "nothing to score"
        return stats

    profile_text = Profile.get_solo().prompt_profile
    size = max(1, s.score_batch_size)
    for i in range(0, len(jobs), size):
        if deadline and time.monotonic() > deadline:
            stats["stopped"] = "time limit"
            break
        batch = jobs[i:i + size]
        ids = [job_ref(j) for j in batch]
        try:
            stats["calls"] += 1
            reply = ask_claude(build_score_prompt(profile_text, batch), purpose="score", items=len(batch))
            results, missing = parse_score_reply(reply, ids)
        except ClaudeLimitError as exc:
            log("error", f"Claude usage limit hit while scoring — stopping. ({exc})")
            stats["stopped"] = "Claude usage limit"
            break
        except (ClaudeError, LLMParseError) as exc:
            log("error", f"Scoring batch failed: {exc}", ids=ids)
            for j in batch:
                j.score_error = str(exc)[:300]
                j.save(update_fields=["score_error", "updated_at"])
            stats["failed"] += len(batch)
            continue
        for j in batch:
            r = results.get(job_ref(j))
            if r:
                apply_score(j, r)
                stats["scored"] += 1
                log("score", f"{r.score} · {j.title} @ {j.company} — {r.reason}", job=j)
            else:
                j.score_error = "Missing from Claude's reply"
                j.save(update_fields=["score_error", "updated_at"])
                stats["failed"] += 1
    return stats


def score_one(job):
    """Score (or re-score) a single job on demand from the dashboard."""
    reply = ask_claude(build_score_prompt(Profile.get_solo().prompt_profile, [job]), purpose="score", items=1)
    results, _ = parse_score_reply(reply, [job_ref(job)])
    r = results.get(job_ref(job))
    if not r:
        raise ClaudeError("Claude didn't return a score for this job.")
    apply_score(job, r)
    log("score", f"{r.score} · {job.title} @ {job.company} — {r.reason}", job=job)
    return job
