"""
Your answer sheet for the questions application forms always ask.

Every answer comes from Settings → Hands-free applying (which you confirm once), never from guessing.
For demographic questions NexScout always picks "prefer not to say / decline".
Anything it can't answer from here is left for you, and that form is not submitted automatically.
"""
import re

from scout.models import Profile, ScoutSettings

DECLINE_RE = re.compile(r"decline|prefer not|don.?t wish|do not wish|not to (say|answer|disclose)|rather not|"
                        r"not specified|choose not|i don.?t want|no answer", re.I)
DEMOGRAPHIC_RE = re.compile(r"gender|\bsex\b|race|ethnic|veteran|disab|hispanic|latin[oa]|sexual orientation|"
                            r"pronoun|transgender|lgbt|military|protected", re.I)
NEVER_RE = re.compile(r"date of birth|birthday|\bdob\b|\bage\b|ssn|social security|national id|passport|bank|"
                      r"criminal|convict|felony|background check|credit check|drug test|security clearance", re.I)

RULES = [
    # (key, regex on the question)
    ("consent", r"consent|i agree|agree to|acknowledg|privacy|terms|gdpr|data processing|retain my|store my|"
                r"i confirm|i certify|certify that|accurate and complete"),
    ("sponsorship", r"sponsor|visa"),
    ("authorized_here", r"authori[sz]ed.*(country|where you|location you|place you).*(work|be working|reside|live)|"
                        r"right to work in (the country|your country)|eligible to work in (the country|your country)"),
    ("authorized", r"authori[sz]|right to work|eligib\w* to work|legally (able|allowed|permitted)|work permit"),
    ("relocate", r"relocat"),
    ("salary_number", r"(salary|compensation|pay|rate).*(usd|\$|number|amount|annual|per year|figure)"),
    ("salary", r"salary|compensation|pay expectation|expected pay|desired pay|\brate\b|remuneration"),
    ("notice", r"notice period|start date|when can you start|earliest start|availability to start|available to start"),
    ("years", r"years of (professional |relevant |work )?experience|how many years"),
    ("degree_done", r"(completed|finished|hold|have) (a |your )?(bachelor|degree)|degree completed"),
    ("education", r"education|degree|university|college|school|qualification"),
    ("linkedin", r"linked\s*in"),
    ("remote_ok", r"(comfortable|okay|ok|happy|able) (with |to )?(work(ing)? )?remote|remote work"),
    ("timezone", r"time\s*zone|timezone|overlap|working hours"),
    ("country", r"country of residence|which country|country you (live|reside)|^country"),
    ("city", r"\bcity\b|current location|where are you (based|located)|^location"),
]

YES = {"yes": "Yes", "no": "No"}


def bank():
    s = ScoutSettings.get_solo()
    p = Profile.get_solo()
    return {
        "consent": "yes" if s.ans_consent else "",
        "sponsorship": s.ans_need_sponsorship,
        "authorized_here": "yes",          # you live and work in Nigeria
        "authorized": s.ans_authorized_elsewhere,
        "authorized_text": s.ans_work_authorization,
        "relocate": s.ans_relocate,
        "salary": s.ans_salary,
        "salary_number": s.ans_salary_number,
        "notice": s.ans_notice_period,
        "years": s.ans_years_experience,
        "degree_done": s.ans_degree_completed,
        "education": s.ans_education,
        "linkedin": s.ans_linkedin,
        "remote_ok": "yes",
        "timezone": "West Africa Time (UTC+1). Full overlap with UK/EU hours and 4-6 hours with US East.",
        "country": "Nigeria",
        "city": "Nigeria",
        "name": p.full_name,
    }


def classify(question):
    q = (question or "").strip()
    if DEMOGRAPHIC_RE.search(q):
        return "demographic"
    if NEVER_RE.search(q):
        return "never"
    for key, rx in RULES:
        if re.search(rx, q, re.I):
            return key
    return None


def mentions_nigeria_or_here(question):
    return bool(re.search(r"nigeria|africa|country (you|where you) (live|reside)|your country", question or "", re.I))


def text_answer(question, kind="text"):
    """Answer for a free-text field from your answer sheet, or None if it isn't a sheet question."""
    key = classify(question)
    b = bank()
    if key in (None, "demographic", "never", "consent"):
        return None
    if key in ("authorized", "authorized_here"):
        return b["authorized_text"]
    if key == "sponsorship":
        return ("No. I work remotely from Nigeria, so a remote role needs no visa sponsorship."
                if b["sponsorship"] == "no" else "Yes, I would need visa sponsorship to relocate.")
    if key == "salary_number" or (key == "salary" and kind == "number"):
        return b["salary_number"] or None
    if key == "years":
        return b["years"]
    if key in ("relocate", "degree_done", "remote_ok"):
        return YES[b[key]] if b[key] in YES else None
    return b.get(key) or None


def choose_option(question, options):
    """
    Pick one option label for a select / radio question from your answer sheet.
    Returns the chosen option text, or None to leave it for you.
    """
    opts = [o for o in options if o and o.strip() and not re.match(r"^(select|choose|--|please select)", o.strip(), re.I)]
    if not opts:
        return None
    key = classify(question)
    b = bank()

    def pick(pattern):
        for o in opts:
            if re.search(pattern, o, re.I):
                return o
        return None

    if key == "demographic":
        return pick(DECLINE_RE.pattern)
    if key == "never":
        return None
    yes_no = None
    if key == "consent":
        yes_no = b["consent"] or None
    elif key == "authorized_here":
        yes_no = "yes"
    elif key == "authorized":
        yes_no = "yes" if mentions_nigeria_or_here(question) else b["authorized"]
    elif key in ("sponsorship", "relocate", "degree_done", "remote_ok"):
        yes_no = b[key]
    if yes_no:
        return pick(rf"^\s*{yes_no}\b") or pick(rf"\b{yes_no}\b")
    if key == "country":
        return pick(r"nigeria")
    if key == "years":
        try:
            years = float(b["years"])
        except ValueError:
            return None
        for o in opts:   # "0-2 years", "1-3", "2+", "Less than 3"
            nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", o)]
            if len(nums) >= 2 and nums[0] <= years <= nums[1]:
                return o
            if len(nums) == 1 and "+" in o and years >= nums[0]:
                return o
            if len(nums) == 1 and re.search(r"less than|under|<", o, re.I) and years < nums[0]:
                return o
        return None
    if key == "education":
        return pick(r"bachelor|b\.?sc|undergrad") if b["degree_done"] == "yes" else pick(r"in progress|currently|some college|student|high school")
    return None
