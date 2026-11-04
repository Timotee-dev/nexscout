"""
DRAFT stage. Cover notes (<150 words) + a one-line opener for high-scoring jobs.
NexScout drafts; you edit, copy and apply yourself.
"""
import re
import time

from django.utils import timezone

from scout.models import Job, Profile, ScoutSettings

from .activity import log
from .claude_client import ClaudeError, ClaudeLimitError, ask_claude
from .llm_json import LLMParseError, as_list_of_dicts, extract_json
from .text import truncate, word_count

MAX_WORDS = 150
DESC_CHARS = 1500

BANNED_PHRASES = [
    "i am writing to express", "i'm writing to express", "express my interest", "passionate", "passion for",
    "rockstar", "rock star", "ninja", "guru", "wizard", "hit the ground running", "team player",
    "synergy", "go-getter", "thrilled", "i am excited to apply", "i'm excited to apply", "dynamic",
    "results-driven", "self-starter", "perfect fit", "leverage my", "delve", "to whom it may concern",
    "dear sir/madam", "i believe i would be a great fit", "esteemed",
]

PROJECT_HINTS = {
    "nexivo": ["payment", "payments", "fintech", "stripe", "paystack", "flutterwave", "billing", "checkout",
               "webhook", "payout", "wallet", "ticket", "event", "invoice", "transaction", "mobile", "react native"],
    "nexvote": ["multi-tenant", "multitenant", "tenant", "saas", "b2b", "dashboard", "real-time", "realtime",
                "organisation", "organization", "platform", "subscription"],
    "nexreach": ["ai", "llm", "gpt", "claude", "openai", "agent", "agents", "machine learning", "ml",
                 "automation", "scraping", "pipeline", "nlp", "prompt"],
    "bts": ["e-commerce", "ecommerce", "marketplace", "vendor", "catalog", "catalogue", "shop", "retail",
            "orders", "inventory", "client", "agency", "frontend", "full-stack", "fullstack"],
}

DRAFT_PROMPT = """You draft job application text for the candidate below. They will edit and send it themselves.
Do not use any tools. Reply with JSON only — no prose, no code fences.

CANDIDATE PROFILE (the ONLY facts you may use)
{profile}

HARD RULES
- Use only facts, numbers, names and experience that appear in the profile. Never invent years of experience,
  employers, metrics, clients or technologies. If the job needs something the candidate lacks, don't claim it.
- cover_note: under {max_words} words (aim for 90–130). First person. Plain, confident, human.
  Start with something specific about this role or company, not with "I". Pick ONE project and give one or two
  concrete facts from it that match the job. End with availability (remote from Nigeria, WAT/UTC+1, open to
  contract or part-time) and a simple next step. No sign-off block, no placeholders like [Name].
- opener: ONE line (max 30 words) to open a LinkedIn or email message to the hiring manager.
- Banned: "I am writing to express my interest", "passionate", "rockstar", "excited to apply", "hit the ground
  running", "team player", "perfect fit", buzzwords and flattery. Avoid em dashes.
- project: the key of the project you used: one of {project_keys}.

JOBS
{jobs}

- cv_summary: 2-3 sentences (max 60 words) for the top of a CV tailored to this job, third-person-free and
  plain ("Backend engineer who..."), using only facts from the profile. Lead with what this job cares about.
- project_order: all project keys, most relevant to this job first.

Return a JSON array, one object per job:
[{{"id": "J123", "project": "nexivo", "cover_note": "...", "opener": "...",
   "cv_summary": "...", "project_order": ["nexivo", "nexvote", "bts", "nexreach"]}}]
"""


def suggest_project(text, project_keys=None):
    """Rule-based hint: which project best matches the job text."""
    t = (text or "").lower()
    scores = {}
    for key, words in PROJECT_HINTS.items():
        if project_keys and key not in project_keys:
            continue
        scores[key] = sum(1 for w in words if re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", t))
    if not scores:
        return None
    best = max(scores, key=lambda k: (scores[k], k == "nexivo"))
    return best if scores[best] > 0 else "nexivo"


def safe_cv_summary(summary, profile, job):
    """Use Claude's tailored summary only if it passes the same truth checks as drafts."""
    summary = (summary or "").strip()
    if not summary or word_count(summary) > 80 or find_cliches(summary):
        return ""
    if unsupported_numbers(summary, profile.prompt_profile, job.title, job.description):
        return ""
    return summary


def find_cliches(text):
    low = (text or "").lower()
    return [p for p in BANNED_PHRASES if p in low]


_NUM_RE = re.compile(r"\b\d[\d,.]*%?")


def unsupported_numbers(draft, *sources):
    """Numbers in the draft that appear in neither the profile nor the listing."""
    corpus = " ".join(s or "" for s in sources)
    out = []
    for n in _NUM_RE.findall(draft or ""):
        n = n.rstrip(".,")
        if n and n not in corpus and n not in out:
            out.append(n)
    return out


def review_draft(cover_note, opener, profile_text, job):
    warnings = []
    words = word_count(cover_note)
    if words >= MAX_WORDS:
        warnings.append(f"Cover note is {words} words (limit {MAX_WORDS}); trim it.")
    cl = find_cliches(f"{cover_note}\n{opener}")
    if cl:
        warnings.append("Cliché check: " + ", ".join(f'"{c}"' for c in cl))
    nums = unsupported_numbers(f"{cover_note} {opener}", profile_text, job.title, job.description, job.salary)
    if nums:
        warnings.append("Numbers not in your CV or the listing: " + ", ".join(nums) + " — verify or remove.")
    if "[" in cover_note and "]" in cover_note:
        warnings.append("Contains a [placeholder].")
    return warnings


def format_job_for_draft(job, project_keys):
    hint = suggest_project(f"{job.title} {' '.join(job.tags)} {job.description}", project_keys)
    return "\n".join([
        f"[J{job.pk}] {job.title} — {job.company or 'Unknown company'} ({job.location or 'remote'})",
        f"Fit notes: score {job.score}; matched {', '.join(job.matched_skills) or '-'}; "
        f"gaps {', '.join(job.missing_skills) or '-'}",
        f"Suggested project: {hint}",
        f"Description: {truncate(job.description, DESC_CHARS)}",
    ])


def parse_draft_reply(text, expected_ids, project_keys):
    items = as_list_of_dicts(extract_json(text), "drafts")
    wanted = {i.upper() for i in expected_ids}
    out = {}
    for item in items:
        ref = str(item.get("id") or "").strip().upper()
        if ref and not ref.startswith("J"):
            ref = f"J{ref}"
        note = str(item.get("cover_note") or "").strip()
        if ref not in wanted or not note:
            continue
        project = str(item.get("project") or "").strip().lower()
        out[ref] = {
            "cover_note": note,
            "opener": str(item.get("opener") or "").strip().splitlines()[0][:400] if item.get("opener") else "",
            "project": project if project in project_keys else "",
            "cv_summary": str(item.get("cv_summary") or "").strip()[:600],
            "project_order": [k for k in (item.get("project_order") or []) if k in project_keys],
        }
    if not out and items:
        raise LLMParseError("Reply had no usable drafts")
    return out


def drafted_today():
    return Job.objects.filter(drafted_at__date=timezone.localdate()).count()


def draftable_jobs(threshold):
    return (Job.objects.filter(score__gte=threshold, drafted_at__isnull=True,
                               status__in=[Job.Status.NEW, Job.Status.SHORTLISTED])
            .exclude(open_to_nigeria=Job.NigeriaFit.NO)
            .order_by("-score", "-posted_at"))


def _draft_batch(batch, profile, s):
    keys = [p["key"] for p in profile.projects or [] if p.get("key")]
    prompt = DRAFT_PROMPT.format(profile=profile.prompt_profile.strip(), max_words=MAX_WORDS,
                                 project_keys=", ".join(keys),
                                 jobs="\n\n---\n\n".join(format_job_for_draft(j, keys) for j in batch))
    reply = ask_claude(prompt, purpose="draft", items=len(batch))
    drafts = parse_draft_reply(reply, [f"J{j.pk}" for j in batch], keys)
    done = []
    for j in batch:
        d = drafts.get(f"J{j.pk}")
        if not d:
            continue
        j.cover_note = d["cover_note"]
        j.opener = d["opener"]
        j.chosen_project = d["project"] or suggest_project(j.description, keys) or ""
        j.draft_warnings = review_draft(j.cover_note, j.opener, profile.prompt_profile, j)
        j.cv_summary = safe_cv_summary(d.get("cv_summary", ""), profile, j)
        j.cv_project_order = d.get("project_order") or []
        j.drafted_at = timezone.now()
        j.save()
        if ScoutSettings.get_solo().tailored_cv:
            try:
                from .cv_builder import build_cv
                build_cv(j)
            except Exception as exc:  # a CV problem must never lose the draft
                log("error", f"Couldn't build tailored CV for {j.title}: {exc}", job=j)
        done.append(j)
        log("draft", f"Drafted note for {j.title} @ {j.company} (using {j.chosen_project})", job=j,
            warnings=j.draft_warnings)
    return done


def draft_pending(limit=None, deadline=None):
    s = ScoutSettings.get_solo()
    remaining = max(0, s.daily_draft_cap - drafted_today())
    stats = {"drafted": 0, "failed": 0, "calls": 0, "cap_left": remaining, "stopped": ""}
    if remaining == 0:
        stats["stopped"] = "daily draft cap reached"
        return stats
    n = min(limit, remaining) if limit else remaining
    jobs = list(draftable_jobs(s.score_threshold)[:n])
    if not jobs:
        stats["stopped"] = f"no jobs scoring {s.score_threshold}+ waiting for a draft"
        return stats
    profile = Profile.get_solo()
    size = max(1, s.draft_batch_size)
    for i in range(0, len(jobs), size):
        if deadline and time.monotonic() > deadline:
            stats["stopped"] = "time limit"
            break
        batch = jobs[i:i + size]
        try:
            stats["calls"] += 1
            done = _draft_batch(batch, profile, s)
        except ClaudeLimitError as exc:
            log("error", f"Claude usage limit hit while drafting — stopping. ({exc})")
            stats["stopped"] = "Claude usage limit"
            break
        except (ClaudeError, LLMParseError) as exc:
            log("error", f"Drafting batch failed: {exc}")
            stats["failed"] += len(batch)
            continue
        stats["drafted"] += len(done)
        stats["failed"] += len(batch) - len(done)
    return stats


def redraft_job(job):
    """Single-job redraft from the dashboard (counts toward the daily cap)."""
    s = ScoutSettings.get_solo()
    if drafted_today() >= s.daily_draft_cap:
        raise ClaudeError("Daily draft cap reached. Raise it in Settings if you really need more today.")
    done = _draft_batch([job], Profile.get_solo(), s)
    if not done:
        raise ClaudeError("Claude didn't return a draft for this job.")
    return done[0]
