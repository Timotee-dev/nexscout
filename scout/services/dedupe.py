"""
Cross-source deduplication: same company + similar title = same job.
"""
import re
from difflib import SequenceMatcher

COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "plc", "gmbh", "ag", "sa", "sas", "bv", "co",
    "corp", "corporation", "company", "pte", "pty", "srl", "oy", "ab", "as", "hq", "group",
}
TITLE_NOISE = {"remote", "fully", "worldwide", "anywhere", "contract", "part", "time", "full",
               "the", "a", "an", "m", "f", "d", "x", "100", "global", "only", "hybrid", "job"}
TITLE_SYNONYMS = {
    "developer": "engineer", "dev": "engineer", "programmer": "engineer", "swe": "engineer",
    "sr": "senior", "jr": "junior", "mid": "mid", "intermediate": "mid",
    "back": "backend", "backend": "backend", "fullstack": "fullstack",
}


def company_key(name):
    s = (name or "").lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    words = [w for w in s.split() if w not in COMPANY_SUFFIXES]
    return " ".join(words).strip()


def title_key(title):
    s = (title or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)  # drop "(Remote)", "[EMEA]"
    s = re.sub(r"\bback[\s-]?end\b", "backend", s)
    s = re.sub(r"\bfull[\s-]?stack\b", "fullstack", s)
    s = re.sub(r"\bfront[\s-]?end\b", "frontend", s)
    s = re.sub(r"[^\w\s]", " ", s)
    words = []
    for w in s.split():
        if w in TITLE_NOISE:
            continue
        words.append(TITLE_SYNONYMS.get(w, w))
    return " ".join(words).strip()


def titles_similar(a_key, b_key, threshold=0.85):
    if not a_key or not b_key:
        return False
    if a_key == b_key:
        return True
    a, b = set(a_key.split()), set(b_key.split())
    jaccard = len(a & b) / len(a | b)
    if jaccard >= 0.8:
        return True
    return SequenceMatcher(None, a_key, b_key).ratio() >= threshold


def is_duplicate(company_a, title_a, company_b, title_b):
    ca, cb = company_key(company_a), company_key(company_b)
    if not ca or ca != cb:
        return False
    return titles_similar(title_key(title_a), title_key(title_b))


def find_duplicate(raw, candidates):
    """Return the first candidate (Job) that duplicates `raw` (RawJob), else None.
    `candidates` should already be narrowed to the same company_key."""
    tk = title_key(raw.title)
    for job in candidates:
        if titles_similar(tk, job.title_key or title_key(job.title)):
            return job
    return None
