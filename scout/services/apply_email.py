"""
Spot listings that take applications by email, e.g.
    "To apply, send your CV to jobs@acme.com with the subject [Backend Engineer @ Acme]"
Returns the address and any required subject line. Rule-based, no AI cost.
"""
import html
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MAILTO_RE = re.compile(r"mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", re.I)
APPLY_WORDS = re.compile(
    r"\b(apply|applying|application|applications|send|email|e-mail|resume|cv|reach out|contact|"
    r"interested|portfolio|cover letter)\b", re.I,
)
# Addresses that are never where applications go.
SKIP_LOCAL = {"noreply", "no-reply", "donotreply", "privacy", "legal", "abuse", "security", "unsubscribe",
              "billing", "press", "media", "support", "help", "info-privacy", "dpo", "gdpr"}
SUBJECT_RE = re.compile(
    r"(?:subject(?:\s*line)?|email\s*title|title\s*of\s*(?:the|your)\s*email|email\s*subject)"
    r"\s*(?:of|:|as|to\s*be|should\s*be|must\s*be|reading|with|=)?\s*"
    r"(\[[^\]\n]{3,150}\]|\"[^\"\n]{3,150}\"|“[^”\n]{3,150}”|'[^'\n]{3,150}')",
    re.I,
)


def _clean_text(text_html, text):
    raw = f"{text or ''}\n{html.unescape(text_html or '')}"
    return re.sub(r"<[^>]+>", " ", raw)


def find_apply_email(text_html="", text=""):
    """Best application address in the listing, or ''."""
    body = _clean_text(text_html, text)
    candidates = []
    for m in MAILTO_RE.finditer(text_html or ""):
        candidates.append((m.group(1), m.start(), True))
    for m in EMAIL_RE.finditer(body):
        candidates.append((m.group(0), m.start(), False))
    best = ""
    for addr, pos, is_mailto in candidates:
        addr = addr.strip(".,;:)").lower()
        local, _, domain = addr.partition("@")
        if local in SKIP_LOCAL or domain.endswith((".png", ".jpg", ".svg", "example.com")):
            continue
        window = body[max(0, pos - 160): pos + 80] if not is_mailto else body
        if APPLY_WORDS.search(window):
            best = addr
            break
    return best


def find_apply_subject(text_html="", text=""):
    body = _clean_text(text_html, text)
    m = SUBJECT_RE.search(body)
    if not m:
        return ""
    subj = m.group(1).strip()
    if subj[0] in "\"“'" and subj[-1] in "\"”'":
        subj = subj[1:-1].strip()
    return subj[:300]


def detect(text_html="", text=""):
    email = find_apply_email(text_html, text)
    return email, (find_apply_subject(text_html, text) if email else "")
