"""
Browser autofill for company application forms (Greenhouse, Lever, Ashby, Workable and most others).

What it does, per job:
  1. Opens the application page in a real browser window on your computer (your Chrome if found).
  2. Uploads your tailored CV, then fills name, email, phone, links, location and cover letter.
  3. Drafts answers to the form's own questions with Claude, using only facts from your profile.
  4. Sensitive questions (work authorisation, visa, salary, notice, consent, demographics) are answered only
     from your answer sheet in Settings (demographics always "prefer not to say"); otherwise left for you.
  5. Highlights everything (green = filled, blue = drafted answer, amber = needs you).
     In the visible browser it STOPS for you to submit. In hands-free mode (autopilot) it submits only when
     every required field is filled, there's no CAPTCHA, and you've confirmed your answer sheet.
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
  const textOf = (n) => (n && (n.innerText || n.textContent) || '').replace(/\s+/g, ' ').trim();
  const els = Array.from(document.querySelectorAll('input, textarea, select'));
  els.forEach((el, i) => {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden','submit','button','image','reset'].includes(type)) return;
    const st = getComputedStyle(el);
    const visible = el.getClientRects().length && st.visibility !== 'hidden' && st.display !== 'none';
    if (!visible && !['file', 'radio', 'checkbox'].includes(type)) return;
    let own = '';
    if (el.id) { try { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) own = textOf(l); } catch (e) {} }
    if (!own) { const l = el.closest('label'); if (l) own = textOf(l); }
    if (!own && el.getAttribute('aria-labelledby')) {
      own = el.getAttribute('aria-labelledby').split(/\s+/).map(id => textOf(document.getElementById(id))).join(' ');
    }
    if (!own) own = el.getAttribute('aria-label') || '';
    let label = own, group = '';
    if (type === 'radio' || type === 'checkbox') {
      // the question for a group of options
      let c = el.parentElement;
      for (let k = 0; k < 6 && c; k++, c = c.parentElement) {
        const same = el.name ? c.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`).length : 1;
        const lg = c.querySelector('legend, [class*="question"], [class*="label"]:not(label), [class*="Label"]:not(label)');
        if (c.tagName === 'FIELDSET' || same > 1 || (lg && !lg.contains(el))) {
          const q = c.querySelector('legend') || lg;
          if (q && !q.contains(el)) { group = textOf(q); break; }
        }
      }
      if (!group) group = own;
    }
    if (!label) {
      let c = el.parentElement;
      for (let k = 0; k < 4 && c && !label; k++, c = c.parentElement) {
        const t = c.querySelector('label, legend, [class*="label"], [class*="question"], [class*="Label"]');
        if (t && !t.contains(el)) label = textOf(t);
      }
    }
    label = (label || el.placeholder || el.name || el.id || '').replace(/\s+/g, ' ').trim().slice(0, 300);
    el.setAttribute('data-nexscout-idx', String(i));
    const req = !!(el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(group || label));
    out.push({
      idx: i, tag: el.tagName.toLowerCase(), type, label, group: (group || '').slice(0, 300),
      name: el.name || '', id: el.id || '', placeholder: el.placeholder || '', required: req,
      options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()) : [],
      checked: !!el.checked,
      filled: (type === 'checkbox' || type === 'radio') ? !!el.checked
              : (el.tagName === 'SELECT' ? !!(el.value !== '' && !/^\s*(select|choose|--|please|-)/i.test((el.options[el.selectedIndex] || {}).text || ''))
              : !!(el.value || '').trim()),
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
SUBMITTED_URL_RE = re.compile(
    r"/(confirmation|confirmed|thank[-_]?you|thanks|success|submitted|application[-_]?(submitted|received|complete))(\b|/|\?|$)",
    re.I,
)

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


CHOICE_PROMPT = """Choose answers for multiple-choice questions on a job application form, for the candidate below.
Do not use any tools. Reply with JSON only.

CANDIDATE PROFILE (the ONLY facts you may use)
{profile}

CANDIDATE'S OWN ANSWER SHEET
{sheet}

JOB: {title} at {company}

RULES
- For each question pick EXACTLY one of the listed options, copied character for character.
- Only pick an option the profile or answer sheet clearly supports. If unsure, return "" for that question.
- Never guess demographic, legal, criminal, medical or identity questions: return "".

QUESTIONS
{questions}

Return: {{"picks": [{{"n": 1, "option": "..."}}]}}
"""


def answer_choices(job, items, ask=None):
    """items: list of (question, [options]) -> list of chosen option text ('' = leave)."""
    if not items:
        return []
    from .answers import bank
    from .claude_client import ask_claude
    from .llm_json import as_list_of_dicts, extract_json
    ask = ask or ask_claude
    sheet = "\n".join(f"- {k}: {v}" for k, v in bank().items() if v)
    qs = "\n".join(f"{i}. {q}\n   Options: " + " | ".join(o for o in opts if o) for i, (q, opts) in enumerate(items, 1))
    reply = ask(CHOICE_PROMPT.format(profile=Profile.get_solo().prompt_profile.strip(), sheet=sheet,
                                     title=job.title, company=job.company or "the company", questions=qs),
                purpose="autofill", items=len(items))
    picks = {}
    for it in as_list_of_dicts(extract_json(reply), "picks"):
        try:
            picks[int(it.get("n"))] = str(it.get("option") or "").strip()
        except (TypeError, ValueError):
            continue
    out = []
    for i, (_, opts) in enumerate(items, 1):
        choice = picks.get(i, "")
        out.append(choice if choice in opts else "")   # must be a real option, never invented
    return out


def _fill(field, value):
    _loc(field).fill(str(value))
    _mark(field, "filled")


def _need(field, counts, question=None):
    _mark(field, "attention")
    counts["attention"] += 1
    if field.get("required"):
        counts["missing"].append((question or field.get("group") or field.get("label") or field.get("name") or "a field")[:120])


def fill_page(page, job, cv_path, ask=None, auto=False):
    """
    Fill whatever form is on the page. Returns counts:
      filled / ai / attention (numbers), missing (required questions left empty), captcha (bool).
    Never submits; see submit_form() for hands-free mode.
    """
    from scout.models import ScoutSettings
    from . import answers as sheet

    s = ScoutSettings.get_solo()
    use_sheet = s.answers_confirmed
    values = profile_values(job, cv_path)
    if s.ans_linkedin:
        values["linkedin"] = s.ans_linkedin
    counts = {"filled": 0, "ai": 0, "attention": 0, "missing": [], "captcha": False}
    fields = scan(page)

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

    fields = scan(page)
    questions, choice_items, radio_groups = [], [], {}
    for f in fields:
        t = f["type"]
        if t == "file":
            if f["required"] and not f["filled"]:
                _need(f, counts)
            continue
        if t == "radio":
            radio_groups.setdefault((id(f["frame"]), f["name"] or f["group"]), []).append(f)
            continue
        if t == "checkbox":
            q = f["label"] or f["group"]
            if f["checked"]:
                continue
            if use_sheet and s.ans_consent and sheet.classify(q) == "consent":
                try:
                    _loc(f).check()
                    _mark(f, "filled")
                    counts["filled"] += 1
                    continue
                except Exception:
                    pass
            if f["required"]:
                _need(f, counts, q)
            continue
        if f["tag"] == "select":
            if f["filled"]:
                continue
            q = f["label"]
            opt = sheet.choose_option(q, f["options"]) if use_sheet else None
            if opt:
                try:
                    _loc(f).select_option(label=opt)
                    _mark(f, "filled")
                    counts["filled"] += 1
                    continue
                except Exception:
                    pass
            if use_sheet and sheet.classify(q) not in ("demographic", "never"):
                choice_items.append(("select", f, q, f["options"]))
            elif f["required"] or sheet.classify(q) == "demographic":
                _need(f, counts, q)
            continue

        key, sensitive = classify(f)
        if sensitive:
            if f["filled"]:
                continue
            ans = sheet.text_answer(f["label"], kind=t) if use_sheet else None
            if ans:
                try:
                    _fill(f, ans)
                    counts["filled"] += 1
                    continue
                except Exception:
                    pass
            _need(f, counts)
            continue
        if f["filled"] and key != "resume":
            continue
        if key and values.get(key):
            try:
                _fill(f, values[key])
                counts["filled"] += 1
            except Exception:
                pass
            continue
        if key:
            if f["required"]:
                _need(f, counts)
            continue
        if f["tag"] == "textarea" or t in ("text", "url", "number", "tel", "email", ""):
            if len(f["label"]) > 3:
                ans = sheet.text_answer(f["label"], kind=t) if use_sheet else None
                if ans:
                    try:
                        _fill(f, ans)
                        counts["filled"] += 1
                        continue
                    except Exception:
                        pass
                if t == "number":
                    if f["required"]:
                        _need(f, counts)
                    continue
                questions.append(f)
            elif f["required"]:
                _need(f, counts)

    # Radio-button questions (Yes/No, ranges, decline-to-answer)
    for group in radio_groups.values():
        if any(g["checked"] for g in group):
            continue
        q = group[0]["group"] or group[0]["label"]
        labels = [g["label"] for g in group]
        required = any(g["required"] for g in group) or q.rstrip().endswith("*")
        for g in group:
            g["required"] = required
        opt = sheet.choose_option(q, labels) if use_sheet else None
        if opt:
            target = next(g for g in group if g["label"] == opt)
            try:
                _loc(target).check()
                for g in group:
                    _mark(g, "filled")
                counts["filled"] += 1
                continue
            except Exception:
                pass
        if use_sheet and sheet.classify(q) not in ("demographic", "never"):
            choice_items.append(("radio", group, q, labels))
        elif required or sheet.classify(q) == "demographic":
            _need(group[0], counts, q)

    # The form's own written questions, answered from your profile
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
                    continue
                except Exception:
                    pass
            if q["required"]:
                _need(q, counts)

    # Remaining multiple-choice questions: Claude may only pick a listed option it can justify
    if choice_items:
        try:
            picks = answer_choices(job, [(q, opts) for _, _, q, opts in choice_items], ask=ask)
        except Exception as exc:
            log("error", f"Autofill couldn't choose options for {job.title}: {exc}", job=job)
            picks = [""] * len(choice_items)
        for (kind, target, q, opts), pick in zip(choice_items, picks):
            try:
                if pick and kind == "select":
                    _loc(target).select_option(label=pick)
                    _mark(target, "ai")
                    counts["ai"] += 1
                    continue
                if pick and kind == "radio":
                    _loc(next(g for g in target if g["label"] == pick)).check()
                    for g in target:
                        _mark(g, "ai")
                    counts["ai"] += 1
                    continue
            except Exception:
                pass
            first = target if kind == "select" else target[0]
            if (first.get("required") if kind == "select" else any(g["required"] for g in target)):
                _need(first, counts, q)

    counts["captcha"] = has_captcha(page)
    counts["missing"] += missing_required(page, already=counts["missing"])
    banner(page, counts, auto=auto)
    return counts


CAPTCHA_URL_RE = re.compile(r"recaptcha|hcaptcha|turnstile|challenges\.cloudflare|arkoselabs|funcaptcha|captcha", re.I)


def has_captcha(page):
    """A visible CAPTCHA challenge (invisible score-based ones don't count). NexScout never tries to solve these."""
    for frame in _frames(page):
        if frame == page.main_frame:
            continue
        if CAPTCHA_URL_RE.search(frame.url or "") and "size=invisible" not in (frame.url or ""):
            try:
                el = frame.frame_element()
                box = el.bounding_box()
                if box and box["width"] > 40 and box["height"] > 40 and el.is_visible():
                    return True
            except Exception:
                continue
    try:
        return bool(page.evaluate("""() => [...document.querySelectorAll('.g-recaptcha:not([data-size="invisible"]), .h-captcha, .cf-turnstile, [class*="captcha" i]')]
                                         .some(e => e.getClientRects().length && e.offsetHeight > 30)"""))
    except Exception:
        return False


def missing_required(page, already=()):
    """Required fields that are still empty after filling (the final safety check before any submit)."""
    out = []
    seen_radio = set()
    for f in scan(page):
        if not f["required"]:
            continue
        if f["type"] == "radio":
            key = (id(f["frame"]), f["name"])
            if key in seen_radio:
                continue
            seen_radio.add(key)
            group_checked = f["frame"].evaluate(
                "(n) => !!document.querySelector(`input[type=radio][name=\"${CSS.escape(n)}\"]:checked`)", f["name"]) if f["name"] else f["checked"]
            if group_checked:
                continue
        elif f["filled"]:
            continue
        q = (f["group"] or f["label"] or f["name"] or "a field")[:120]
        if q not in already and q not in out:
            out.append(q)
    return out


PICK_SUBMIT_JS = r"""
() => {
  // the <form> holding most of the fields NexScout filled, then its submit button
  const filled = [...document.querySelectorAll('[data-nexscout-state]')];
  const score = new Map();
  filled.forEach(el => { const f = el.closest('form') || document.body; score.set(f, (score.get(f) || 0) + 1); });
  const form = [...score.entries()].sort((a, b) => b[1] - a[1]).map(e => e[0])[0];
  if (!form) return false;
  const cands = [...form.querySelectorAll('button, input[type=submit]')].filter(b => b.getClientRects().length && !b.disabled);
  const text = b => (b.innerText || b.value || '').trim().toLowerCase();
  const btn = cands.find(b => /submit|send application|apply now|^apply$|complete application/.test(text(b)) && !/linkedin|indeed|google|save|draft|cancel/.test(text(b)))
           || cands.find(b => (b.type || '').toLowerCase() === 'submit' && !/search|subscribe|linkedin/.test(text(b)));
  if (!btn) return false;
  btn.setAttribute('data-nexscout-submit', '1');
  return true;
}
"""


def submit_form(page, timeout_s=20):
    """Click the application form's own submit button once and wait for a confirmation.
    Returns True only if a confirmation was seen."""
    frames = sorted(_frames(page), key=lambda fr: -_safe_count(fr, "[data-nexscout-state]"))
    clicked = False
    for frame in frames:
        try:
            if frame.evaluate(PICK_SUBMIT_JS):
                frame.locator("[data-nexscout-submit]").first.click()
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        return False
    for _ in range(timeout_s * 2):
        page.wait_for_timeout(500)
        if looks_submitted(page):
            return True
    return False


def _safe_count(frame, sel):
    try:
        return frame.locator(sel).count()
    except Exception:
        return 0


def banner(page, counts, submitted=False, auto=False):
    if auto and not submitted:
        html = "<b>NexScout autopilot</b> is filling this application."
    elif submitted:
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
            if SUBMITTED_URL_RE.search(frame.url or ""):
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


def mark_submitted(job, by="you", proof=""):
    job.refresh_from_db()
    if job.status not in (Job.Status.APPLIED, Job.Status.INTERVIEWING, Job.Status.OFFER):
        job.set_status(Job.Status.APPLIED)
    job.apply_queue, job.apply_error = "", ""
    job.form_submitted_at = timezone.now()
    job.applied_via, job.applied_by = "form", by
    if proof:
        job.apply_proof = str(proof)
    job.save(update_fields=["apply_queue", "apply_error", "form_submitted_at", "applied_via", "applied_by",
                            "apply_proof", "updated_at"])
    log("apply", f"Application submitted on the company form: {job.title} @ {job.company}"
                 + (" (by autopilot)" if by == "autopilot" else ""), job=job)
