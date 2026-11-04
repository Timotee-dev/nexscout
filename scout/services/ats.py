"""Recognise applicant-tracking systems from a URL."""
import re

PATTERNS = [
    ("greenhouse", re.compile(r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"https?://jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("workable", re.compile(r"https?://apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)),
]
IGNORE_SLUGS = {"embed", "jobs", "api", "v1", "careers"}


def detect_ats(*urls):
    """-> (ats, company_slug) or ("", "")."""
    for url in urls:
        for name, rx in PATTERNS:
            m = rx.search(url or "")
            if m and m.group(1).lower() not in IGNORE_SLUGS:
                return name, m.group(1)
    return "", ""
