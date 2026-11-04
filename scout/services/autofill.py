"""
Browser autofill for company application forms (Greenhouse, Lever, Ashby, Workable and most others).

What it does, per job:
  1. Opens the application page in a real browser window on your computer (your Chrome if found).
  2. Uploads your tailored CV, then fills name, email, phone, links, location and cover letter.
  3. Drafts answers to the form's own questions with Claude, using only facts from your profile.
  4. Leaves sensitive questions (work authorisation, visa, salary, demographics, consent) for you.
  5. Highlights everything (green = filled, blue = drafted answer to read, amber = needs you)
     and STOPS. It never clicks Submit. When you submit, it notices and marks the job Applied.
"""
import json
import re

from django.utils import timezone

from scout.models import Job, Profile

from .activity import log
from .profile_seed import CV

SCAN_JS = r"""
() => {
  const out = [];
  const els = Array.from(document.querySelectorAll('input, textarea, select'));
  els.forEach((el, i) => {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden','submit','button','image','reset'].includes(type)) return;
    const rect = el.getClientRects().length;
    const st = getComputedStyle(el);
    const visible = rect && st.visibility !== 'hidden' && st.display !== 'none';
    if (!visible && type !== 'file') return;
    let label = '';
    if (el.id) { try { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) label = l.innerText; } catch (e) {} }
    if (!label) { const l = el.closest('label'); if (l) label = l.innerText; }
    if (!label && el.getAttribute('aria-labelledby')) {
      label = el.getAttribute('aria-labelledby').split(/\s+/).map(id => (document.getElementById(id) || {}).innerText || '').join(' ');
    }
    if (!label) label = el.getAttribute('aria-label') || '';
    if (!label) {
      let c = el.parentElement;
      for (let k = 0; k < 4 && c && !label; k++, c = c.parentElement) {
        const t = c.querySelector('label, legend, [class*="label"], [class*="question"], [class*="Label"]');
        if (t && !t.contains(el)) label = t.innerText;
      }
    }
    label = (label || el.placeholder || el.name || el.id || '').replace(/\s+/g, ' ').trim().slice(0, 300);
    el.setAttribute('data-nexscout-idx', String(i));
    out.push({
      idx: i, tag: el.tagName.toLowerCase(), type, label, name: el.name || '', id: el.id || '',
      placeholder: el.placeholder || '',
      required: !!(el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label)),
      filled: (type === 'checkbox' || type === 'radio') ? el.checked : !!(el.value || '').trim(),
    });
  });
  return out;
}
"""

MARK_JS = r"""
([idx, state]) => {
  const el = document.querySelector(`[data-nexscout-idx="${idx}"]`);
  if (el) el.setAttribute('data-nexscout-state', state);
  if (!document.getElementById('nexscout-style')) {
    const s = document.createElement('style'); s.id = 'nexscout-style';
    s.textContent = `[data-nexscout-state="filled"]{outline:2px solid #16a36d!important;outline-offset:2px}
      [data-nexscout-state="ai"]{outline:2px solid #2453d6!important;outline-offset:2px}
      [data-nexscout-state="attention"]{outline:3px solid #d29a2c!important;outline-offset:2px;background:#fff8ea!important}
      #nexscout-banner{position:fixed;left:12px;right:12px;bottom:12px;z-index:2147483647;background:#0f1729;color:#fff;
        font:14px/1.5 system-ui,Segoe UI,sans-serif;padding:12px 16px;border-radius:10px;box-shadow:0 10px 30px rgba(0,0,0,.3)}
      #nexscout-banner b{color:#9fb8ff}`;
    document.head.appendChild(s);
  }
}
"""

BANNER_JS = r"""
(html) => {
  let b = document.getElementById('nexscout-banner');
  if (!b) { b = document.createElement('div'); b.id = 'nexscout-banner'; document.body.appendChild(b); }
  b.innerHTML = html;
}
"""

SUBMITTED_RE = re.compile(
    r"thank(s| you) for (applying|your application|your interest)|application (has been |was )?(submitted|received|sent)"
    r"|we('ve| have) received your application|your application is (in|on its way)|successfully applied",
    re.I,
)
SUBMITTED_URL_RE = re.compile(r"confirm|thank|submitted|success|applied", re.I)

FIELD_RULES = [
    ("resume", r"resum|résumé|\bcv\b|curriculum"),
    ("first_name", r"first[\s_-]*name|given[\s_-]*name|forename|\bfname\b"),
    ("last_name", r"last[\s_-]*name|surname|family[\s_-]*name|\blname\b"),
    ("email", r"e-?mail"),
    ("phone", r"phone|mobile|whats\s?app|telephone"),
    ("linkedin", r"linked\s*in"),
    ("github", r"git\s*hub"),
    ("website", r"website|portfolio|personal (site|url|link)|other (url|link)"),
    ("heard", r"how did you (hear|find)|where did you (hear|find|see)|referral source|source of application"),
    ("company", r"current (company|employer|organi[sz]ation)|^org$|^company$"),
    ("location", r"current location|where are you (based|located)|country of residence|^location|\bcity\b|^country"),
    ("cover_letter", r"cover\s*letter|additional information|anything else|message to (the )?hiring|^comments?$|why .{0,40}(join|interested|apply|want|excite)"),
    ("full_name", r"(^|\b)(full[\s_-]*)?name\b|_systemfield_name"),
]
SENSITIVE_RE = re.compile(
    r"authori[sz]|sponsor|visa|work permit|right to work|eligib|salary|compensation|pay (expectation|range)|expected pay"
    r"|\brate\b|gender|\bsex\b|race|ethnic|veteran|disab|pronoun|hispanic|latin[oa]|sexual|criminal|background check"
    r"|convict|\beeo|consent|i agree|acknowledg|privacy|gdpr|terms|date of birth|birthday|\bage\b|citizen|clearance"
    r"|relocat|notice period|start date|security|ssn|social security|national id|passport|bank",
    re.I,
)

QUESTIONS_PROMPT = """Answer job application form questions for the candidate below. They will read every answer before
submitting. Do not use any tools. Reply with JSON only.

CANDIDATE PROFILE (the ONLY facts you may use)
{profile}

JOB: {title} at {company}
{description}

RULES
- Use only facts from the profile. Never invent experience, numbers, employers, dates or opinions about the company.
- If a question can't be answered truthfully from the profile, return "" for it. Short is fine.
- Plain, confident, first person, no clichés ("passionate", "rockstar"), no em dashes. Max 90 words each.

QUESTIONS
{questions}

Return: {{"answers": [{{"n": 1, "answer": "..."}}]}}
"""


def profile_values(job, cv_path):
    p = Profile.get_solo()
    parts = (p.full_name or CV["full_name"]).split()
    first, last = parts[0], parts[-1] if len(parts) > 1 else ""
    from .sources import get_sources
    src = get_sources().get(job.source)
    return {
        "first_name": first, "last_name": last, "full_name": f"{first} {last}".strip(),
        "email": p.email, "phone": p.phone, "linkedin": "", "github": p.github, "website": p.portfolio,
        "location": "Nigeria", "company": "Independent (Nexora)",
        "heard": "Company careers page" if job.source == "careers" else (src.label if src else "Job board"),
        "cover_letter": job.cover_note or "", "resume": str(cv_path) if cv_path else "",
    }


def classify(field):
    """-> (key or None, sensitive: bool)"""
    text = " ".join([field.get("label", ""), field.get("name", ""), field.get("id", ""), field.get("placeholder", "")])
    if field["type"] == "file":
        if re.search(r"cover", text, re.I):
            return None, False
        return "resume", False
    if SENSITIVE_RE.search(field.get("label", "") or text):
        return None, True
    if field["type"] in ("checkbox", "radio") or field["tag"] == "select":
        return None, False
    for key, rx in FIELD_RULES:
        if key == "resume":
            continue
        if re.search(rx, text, re.I):
            # "Company name" / "hiring manager name" etc. are not your name
            if key == "full_name" and re.search(r"company|manager|referr|school|university|employer", text, re.I):
                return None, False
            if key == "cover_letter" and field["tag"] != "textarea" and field["type"] not in ("text", ""):
                return None, False
            return key, False
    return None, False


def _frames(page):
    return [f for f in page.frames if not f.is_detached()]


def scan(page):
    fields = []
    for frame in _frames(page):
        try:
            for f in frame.evaluate(SCAN_JS):
                f["frame"] = frame
                fields.append(f)
        except Exception:
            continue
    return fields


def _mark(field, state):
    try:
        field["frame"].evaluate(MARK_JS, [field["idx"], state])
    except Exception:
        pass


def _loc(field):
    return field["frame"].locator(f'[data-nexscout-idx="{field["idx"]}"]')


def find_form(page, max_hops=2):
    """Follow an 'Apply' link/button if the page has no form yet."""
    for _ in range(max_hops + 1):
        text_fields = [f for f in scan(page) if f["type"] in ("text", "email", "tel", "textarea", "file", "url")]
        if len(text_fields) >= 3:
            return True
        clicked = False
        for frame in _frames(page):
            for sel in ['a[href*="greenhouse.io"]', 'a[href*="lever.co"]', 'a[href*="ashbyhq.com"]',
                        'a[href*="workable.com"]', 'a:has-text("Apply for this job")', 'a:has-text("Apply now")',
                        'button:has-text("Apply for this job")', 'button:has-text("Apply now")',
                        'a:has-text("Apply")', 'button:has-text("Apply")']:
                loc = frame.locator(sel).first
                try:
                    if loc.count() and loc.is_visible():
                        href = loc.get_attribute("href") if loc.evaluate("e => e.tagName") == "A" else None
                        if href and not href.startswith(("#", "javascript")):
                            page.goto(href if "://" in href else loc.evaluate("e => e.href"), wait_until="domcontentloaded")
                        else:
                            loc.click()
                        page.wait_for_timeout(2500)
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break
        if not clicked:
            return False
    return False


def answer_questions(job, questions, ask=None):
    """questions: list of label strings -> list of answers ('' when unsure)."""
    if not questions:
        return []
    from .claude_client import ask_claude
    from .llm_json import as_list_of_dicts, extract_json
    from .text import truncate
    ask = ask or ask_claude
    prompt = QUESTIONS_PROMPT.format(
        profile=Profile.get_solo().prompt_profile.strip(), title=job.title, company=job.company or "the company",
        description=truncate(job.description, 1500),
        questions="\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1)),
    )
    reply = ask(prompt, purpose="autofill", items=len(questions))
    items = as_list_of_dicts(extract_json(reply), "answers")
    by_n = {int(it.get("n", 0)): str(it.get("answer") or "").strip() for it in items if str(it.get("n", "")).isdigit()}
    return [by_n.get(i, "") for i in range(1, len(questions) + 1)]


def fill_page(page, job, cv_path, ask=None):
    """Fill whatever form is on the page. Returns counts. Never submits."""
    values = profile_values(job, cv_path)
    fields = scan(page)
    counts = {"filled": 0, "ai": 0, "attention": 0}

    # 1) CV first: some systems (Lever) parse it and overwrite fields, so text goes in after.
    for f in fields:
        key, _ = classify(f)
        if key == "resume" and values["resume"] and not f["filled"]:
            try:
                _loc(f).set_input_files(values["resume"])
                _mark(f, "filled")
                counts["filled"] += 1
                page.wait_for_timeout(2500)
                break
            except Exception:
                continue

    # 2) Standard fields
    fields = scan(page)
    questions = []
    for f in fields:
        key, sensitive = classify(f)
        if f["type"] == "file":
            continue
        if sensitive:
            if not f["filled"]:
                _mark(f, "attention")
                counts["attention"] += 1
            continue
        if key and values.get(key):
            try:
                _loc(f).fill(values[key])
                _mark(f, "filled")
                counts["filled"] += 1
            except Exception:
                pass
            continue
        if key or f["type"] in ("checkbox", "radio") or f["tag"] == "select":
            # a known field we have no value for (e.g. LinkedIn), or a choice we won't guess
            if f["required"] and not f["filled"]:
                _mark(f, "attention")
                counts["attention"] += 1
            continue
        if f["tag"] == "textarea" or f["type"] in ("text", "url", ""):
            if not f["filled"] and len(f["label"]) > 3:
                questions.append(f)

    # 3) The form's own questions, answered from your profile (you'll read them)
    questions = questions[:10]
    if questions:
        try:
            answers = answer_questions(job, [q["label"] for q in questions], ask=ask)
        except Exception as exc:
            log("error", f"Autofill couldn't draft answers for {job.title}: {exc}", job=job)
            answers = [""] * len(questions)
        for q, a in zip(questions, answers):
            if a:
                try:
                    _loc(q).fill(a)
                    _mark(q, "ai")
                    counts["ai"] += 1
                except Exception:
                    pass
            elif q["required"]:
                _mark(q, "attention")
                counts["attention"] += 1

    banner(page, counts)
    return counts


def banner(page, counts, submitted=False):
    if submitted:
        html = "<b>Submitted.</b> NexScout marked this job Applied and set your follow-up. Close this tab for the next one."
    else:
        html = (f"<b>NexScout filled {counts['filled']} field(s)</b> (green)"
                + (f", drafted <b>{counts['ai']}</b> answer(s) to read (blue)" if counts["ai"] else "")
                + (f", and <b>{counts['attention']}</b> need you (amber)" if counts["attention"] else "")
                + ". Check everything, then click <b>Submit</b> yourself. NexScout never submits for you.")
    for frame in [page.main_frame]:
        try:
            frame.evaluate(MARK_JS, [-1, ""])
            frame.evaluate(BANNER_JS, html)
        except Exception:
            pass


def looks_submitted(page):
    for frame in _frames(page):
        try:
            if SUBMITTED_URL_RE.search(frame.url or "") and frame.url != "about:blank":
                text = frame.evaluate("() => document.body ? document.body.innerText.slice(0, 4000) : ''")
                if SUBMITTED_RE.search(text) or "confirm" in frame.url.lower():
                    return True
            text = frame.evaluate("() => document.body ? document.body.innerText.slice(0, 4000) : ''")
            if SUBMITTED_RE.search(text):
                return True
        except Exception:
            continue
    return False


def mark_filled(job):
    job.apply_queue = Job.ApplyQueue.FILLED
    job.autofilled_at = timezone.now()
    job.save(update_fields=["apply_queue", "autofilled_at", "updated_at"])


def mark_submitted(job):
    job.refresh_from_db()
    if job.status not in (Job.Status.APPLIED, Job.Status.INTERVIEWING, Job.Status.OFFER):
        job.set_status(Job.Status.APPLIED)
    job.apply_queue = ""
    job.save(update_fields=["apply_queue", "updated_at"])
    log("apply", f"Application submitted on the company form: {job.title} @ {job.company}", job=job)
