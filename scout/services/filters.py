"""
Rule-based filtering. No AI cost. Pure functions so they're easy to test.

    cfg = FilterConfig.from_settings(ScoutSettings.get_solo())
    result = evaluate(raw_job, cfg)
    result.passed, result.reasons
"""
import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

# ---------------------------------------------------------------- config

# Titles that are almost never a Python backend dev role. Editable in Settings.
DEFAULT_EXCLUDED_TITLES = [
    "analyst", "analytics", "data scientist", "data engineer", "solutions engineer", "sales",
    "support", "designer", "marketing", "recruiter", "account executive", "customer success",
    "product manager", "project manager", "writer", "sre", "qa", "ios", "android", "salesforce",
]


@dataclass
class FilterConfig:
    keywords: list = field(default_factory=lambda: ["Django", "Python", "backend", "DRF", "full-stack Python"])
    allowed_locations: list = field(default_factory=lambda: ["Worldwide", "Anywhere", "Global", "Africa", "EMEA", "Nigeria"])
    block_senior_titles: bool = True
    max_years_required: int = 5  # drop if a job asks for >= this many years; 0 disables
    max_age_days: int = 14
    excluded_titles: list = field(default_factory=lambda: list(DEFAULT_EXCLUDED_TITLES))

    @classmethod
    def from_settings(cls, s):
        return cls(
            keywords=s.keyword_list,
            allowed_locations=s.location_list,
            block_senior_titles=s.block_senior_titles,
            max_years_required=s.max_years_required,
            max_age_days=s.max_age_days,
            excluded_titles=s.excluded_title_list,
        )


@dataclass
class FilterResult:
    keyword_match: bool
    passed: bool
    reasons: list
    matched_keywords: list


# ---------------------------------------------------------------- helpers


def _norm(text):
    text = (text or "").lower()
    text = text.replace("‐", "-").replace("–", "-").replace("—", "-")
    text = re.sub(r"\bback[\s-]end\b", "backend", text)
    text = re.sub(r"\bfull[\s-]stack\b", "fullstack", text)
    return text


def _has_word(text, word):
    return re.search(r"(?<![\w])" + re.escape(word) + r"(?![\w])", text) is not None


# ---------------------------------------------------------------- keywords


# Broad words: they only count when they're in the job's TITLE or TAGS.
# ("Python a plus" in a Risk Analyst description shouldn't make it a Python job.)
GENERIC_WORDS = {"python", "backend", "fullstack", "software", "engineer", "developer", "api", "web"}


def match_keywords(title, tags, description, keywords):
    """
    A keyword matches if all of its words appear in the title or tags.
    Specific keywords (e.g. Django, DRF) may also match from the description alone.
    """
    head = _norm(" ".join([title or "", " ".join(tags or [])]))
    full = _norm(" ".join([head, description or ""]))
    hits = []
    for kw in keywords:
        words = _norm(kw).replace("-", " ").split()
        if not words:
            continue
        if all(_has_word(head, w) for w in words):
            hits.append(kw)
        elif any(w not in GENERIC_WORDS for w in words) and all(_has_word(full, w) for w in words):
            hits.append(kw)
    return hits


def check_title(title, excluded):
    t = _norm(title)
    for word in excluded:
        w = _norm(word).strip()
        if w and _has_word(t, w):
            return f"Not a backend dev role ({word})"
    return None


# ---------------------------------------------------------------- location

OPEN_LOCATION_VALUES = {
    "", "remote", "100% remote", "fully remote", "remote job", "remote only", "remote-first",
    "anywhere", "worldwide", "global", "anywhere in the world", "work from anywhere",
}

US_WORDS = ["us", "usa", "u.s.", "u.s.a.", "united states", "america", "north america", "americas",
            "canada", "est", "pst", "cst", "et", "pt"]
EU_WORDS = ["eu", "europe", "european union", "eea", "cet", "germany", "france", "spain", "netherlands",
            "poland", "portugal", "italy", "ireland", "sweden", "cee"]
UK_WORDS = ["uk", "u.k.", "united kingdom", "britain", "england", "gb", "gmt", "bst", "london"]

_TZ_RE = re.compile(r"\b(?:utc|gmt)\s*([+\-−]\s*\d{1,2})(?::\d{2})?", re.I)
MY_UTC_OFFSET = 1  # WAT


def _region_label(loc):
    for words, label in ((UK_WORDS, "UK-only"), (EU_WORDS, "EU-only"), (US_WORDS, "US-only")):
        if any(_has_word(loc, w) for w in words):
            return label
    return None


def _timezone_ok(loc):
    """True/False if the location is a timezone range we can judge, else None."""
    offsets = [int(m.replace(" ", "").replace("−", "-")) for m in _TZ_RE.findall(loc)]
    if not offsets:
        return None
    if len(offsets) >= 2:
        lo, hi = min(offsets), max(offsets)
        return lo - 1 <= MY_UTC_OFFSET <= hi + 1
    return abs(offsets[0] - MY_UTC_OFFSET) <= 3


def check_location(location, allowed_locations):
    """Return None if OK, else a short reason string."""
    loc = _norm(location).strip(" .")
    loc = re.sub(r"\s+", " ", loc)
    if loc in OPEN_LOCATION_VALUES:
        return None
    # "South Africa" alone shouldn't count as "Africa".
    loc_for_allow = loc.replace("south africa", "")
    for allowed in allowed_locations:
        a = _norm(allowed).strip()
        if a and _has_word(loc_for_allow, a):
            return None
    tz = _timezone_ok(loc)
    if tz is True:
        return None
    if tz is False:
        return f"Timezone doesn't fit WAT: {location}"
    label = _region_label(loc)
    if label:
        return f"{label} ({location})"
    return f"Location restricted: {location}"


# ---------------------------------------------------------------- work authorisation

_OK_AUTH_TARGETS = ("nigeria", "country", "your", "their", "any", "which", "where", "whichever", "a country", "the country")
_AUTH_PATTERNS = [
    re.compile(r"(?:must|need to|needs to|required to|should)\s+(?:be\s+)?(?:legally\s+)?"
               r"(?:authori[sz]ed|eligible|permitted|able)\s+to\s+work\s+in\s+(?:the\s+)?([a-z .]{2,40})", re.I),
    re.compile(r"\b(?:work\s+)?authori[sz]ation\s+to\s+work\s+in\s+(?:the\s+)?([a-z .]{2,40})", re.I),
    re.compile(r"(?:must|should|need to)\s+(?:be\s+)?(?:based|located|residing|reside|live|living)\s+in\s+"
               r"(?:the\s+)?([a-z .]{2,40})", re.I),
]
_HARD_AUTH = [
    (re.compile(r"\b(?:us|u\.s\.|usa|uk|eu)\s+(?:citizens?|residents?|persons?)\s+only\b", re.I), "Citizens/residents only"),
    (re.compile(r"\b(?:us|u\.s\.)\s+citizenship\s+(?:is\s+)?required\b", re.I), "US citizenship required"),
    (re.compile(r"\bsecurity\s+clearance\b", re.I), "Security clearance required"),
    (re.compile(r"\bw-?2\s+(?:only|employees?|position|role)\b", re.I), "US W-2 only"),
    (re.compile(r"\bright\s+to\s+work\s+in\s+the\s+uk\b", re.I), "Needs UK right to work"),
]


def check_work_authorisation(text):
    t = text or ""
    for rx, label in _HARD_AUTH:
        if rx.search(t):
            return label
    for rx in _AUTH_PATTERNS:
        for m in rx.finditer(t):
            target = m.group(1).strip(" .").lower()
            if any(target.startswith(ok) for ok in _OK_AUTH_TARGETS):
                continue
            if any(_has_word(target, w) for w in ("nigeria", "africa", "anywhere", "worldwide", "emea")):
                continue
            first = " ".join(target.split()[:3])
            return f"Must be authorised/based in {first}"
    return None


# ---------------------------------------------------------------- seniority

SENIOR_TITLE_RE = re.compile(
    r"\b(senior|sr\.?|staff|lead|principal|head of|director|architect|vp|chief|manager|distinguished)\b", re.I
)
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15}
_YEARS_RE = re.compile(
    r"(?:(?:at least|minimum(?: of)?|min\.?|over|more than)\s+)?"
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|to|or)\s*\d{1,2}\s*\+?\s*)?"
    r"(?:years?|yrs?)(?:'s?)?"
    r"(?:\s+of)?[\s\w,/&()+.-]{0,50}?\bexperience",
    re.I,
)


def required_years(text):
    """Smallest 'N years ... experience' figure mentioned, or None."""
    t = _norm(text)
    for word, num in _NUM_WORDS.items():
        t = re.sub(rf"\b{word}\b", str(num), t)
    found = [int(m.group(1)) for m in _YEARS_RE.finditer(t)]
    found = [n for n in found if 0 < n <= 25]
    return min(found) if found else None


def check_seniority(title, description, cfg):
    if cfg.block_senior_titles:
        m = SENIOR_TITLE_RE.search(title or "")
        if m:
            return f"Senior-level title ({m.group(1).strip()})"
    if cfg.max_years_required:
        years = required_years(description)
        if years is not None and years >= cfg.max_years_required:
            return f"Requires {years}+ years"
    return None


# ---------------------------------------------------------------- age


def check_age(posted_at, max_age_days, now=None):
    if not posted_at or not max_age_days:
        return None
    now = now or timezone.now()
    age = now - posted_at
    if age > timedelta(days=max_age_days):
        return f"Older than {max_age_days} days ({age.days}d)"
    return None


# ---------------------------------------------------------------- main


def below_min_score_reason(score, min_score):
    """Reason string if a scored job falls under your minimum score, else None."""
    if score is not None and min_score and score < min_score:
        return f"Fit score {score} is below your minimum ({min_score})"
    return None


def evaluate(job, cfg, now=None):
    """job: anything with title, tags, description, location, posted_at (RawJob or Job)."""
    hits = match_keywords(job.title, job.tags, job.description, cfg.keywords)
    reasons = []
    for reason in (
        check_title(job.title, cfg.excluded_titles),
        check_location(job.location, cfg.allowed_locations),
        check_work_authorisation(f"{job.location}\n{job.description}"),
        check_seniority(job.title, job.description, cfg),
        check_age(job.posted_at, cfg.max_age_days, now=now),
    ):
        if reason:
            reasons.append(reason)
    if not hits:
        reasons.insert(0, "No keyword match")
    return FilterResult(keyword_match=bool(hits), passed=bool(hits) and not reasons,
                        reasons=reasons, matched_keywords=hits)
