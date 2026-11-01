"""
Direct outreach: short personal messages to companies YOU chose. You copy and send them yourself.
NexScout never visits the LinkedIn URLs you paste and never sends anything.
"""
from django.utils import timezone

from scout.models import OutreachTarget, Profile, ScoutSettings

from .activity import log
from .claude_client import ClaudeError, ClaudeLimitError, ask_claude
from .drafting import find_cliches, suggest_project, unsupported_numbers
from .llm_json import LLMParseError, as_list_of_dicts, extract_json
from .text import word_count

MAX_WORDS = 90

OUTREACH_PROMPT = """Draft short, personal cold messages from the candidate below to companies they chose.
They will edit and send each one themselves. Do not use any tools. Reply with JSON only.

CANDIDATE PROFILE (the ONLY facts you may use)
{profile}

RULES
- Under {max_words} words each. Plain, warm, specific, no flattery, no buzzwords, no em dashes.
- Address the contact by first name if given, otherwise "Hi {{company}} team".
- One line on what the company does (only from the notes given; don't invent details about them).
- One relevant project of the candidate with one concrete fact from the profile.
- A low-pressure ask: a short call, or whether they need backend help on contract/part-time.
- LinkedIn messages: no subject line. Email: start with "Subject: ..." on the first line.
- Never invent numbers, clients or experience. Banned: "passionate", "I hope this finds you well", "rockstar".
- project must be one of: {project_keys}

COMPANIES
{targets}

Return: [{{"id": "O12", "project": "nexivo", "message": "..."}}]
"""


def format_target(t, keys):
    hint = suggest_project(f"{t.what_they_do} {t.angle}", keys)
    return "\n".join([
        f"[O{t.pk}] {t.company_name} ({t.website or 'no website given'})",
        f"Contact: {t.contact_name or 'unknown'}{', ' + t.contact_role if t.contact_role else ''} | Channel: {t.channel}",
        f"What they do (candidate's notes): {t.what_they_do or '-'}",
        f"Angle: {t.angle or '-'} | Suggested project: {hint}",
    ])


def drafted_today():
    return OutreachTarget.objects.filter(drafted_at__date=timezone.localdate()).count()


def draft_outreach(targets):
    s = ScoutSettings.get_solo()
    remaining = s.daily_outreach_cap - drafted_today()
    if remaining <= 0:
        raise ClaudeError("Daily outreach draft cap reached (change it in Settings).")
    targets = list(targets)[:remaining]
    if not targets:
        return []
    profile = Profile.get_solo()
    keys = [p["key"] for p in profile.projects or [] if p.get("key")]
    done = []
    for i in range(0, len(targets), 3):
        batch = targets[i:i + 3]
        prompt = OUTREACH_PROMPT.format(profile=profile.prompt_profile.strip(), max_words=MAX_WORDS,
                                        project_keys=", ".join(keys),
                                        targets="\n\n".join(format_target(t, keys) for t in batch))
        try:
            reply = ask_claude(prompt, purpose="outreach", items=len(batch))
            items = as_list_of_dicts(extract_json(reply), "messages")
        except ClaudeLimitError:
            raise
        except (ClaudeError, LLMParseError) as exc:
            for t in batch:
                t.draft_error = str(exc)[:300]
                t.save(update_fields=["draft_error", "updated_at"])
            log("error", f"Outreach drafting failed: {exc}")
            continue
        by_id = {str(it.get("id", "")).upper(): it for it in items}
        for t in batch:
            it = by_id.get(f"O{t.pk}")
            if not it or not str(it.get("message") or "").strip():
                t.draft_error = "Missing from Claude's reply"
                t.save(update_fields=["draft_error", "updated_at"])
                continue
            t.message = str(it["message"]).strip()
            project = str(it.get("project") or "").lower()
            t.chosen_project = project if project in keys else ""
            warnings = []
            if word_count(t.message) > MAX_WORDS + 10:
                warnings.append(f"{word_count(t.message)} words — trim")
            cl = find_cliches(t.message)
            if cl:
                warnings.append("clichés: " + ", ".join(cl))
            nums = unsupported_numbers(t.message, profile.prompt_profile, t.what_they_do, t.angle)
            if nums:
                warnings.append("check numbers: " + ", ".join(nums))
            t.draft_error = "; ".join(warnings)[:300]
            t.drafted_at = timezone.now()
            if t.status == OutreachTarget.Status.TO_DRAFT:
                t.status = OutreachTarget.Status.DRAFTED
            t.save()
            done.append(t)
            log("outreach", f"Drafted outreach message for {t.company_name}")
    return done
