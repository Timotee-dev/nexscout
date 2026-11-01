"""Small text helpers shared by sources, filters and prompts."""
import html
import re
from datetime import datetime, timezone as dt_tz
from email.utils import parsedate_to_datetime

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</?(p|br|li|ul|ol|div|h[1-6]|tr)[^>]*>", re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def html_to_text(value):
    if not value:
        return ""
    text = _BLOCK_RE.sub("\n", value)
    text = _TAG_RE.sub("", text)
    text = html.unescape(html.unescape(text))
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _NL_RE.sub("\n\n", text).strip()


def truncate(text, limit):
    text = text or ""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + " …"


def parse_datetime_any(value):
    """ISO 8601, RFC 822 (RSS) or unix epoch -> aware UTC datetime, or None."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            return datetime.fromtimestamp(int(value), tz=dt_tz.utc)
        value = str(value).strip()
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_tz.utc)
        return dt
    except (ValueError, TypeError, OverflowError):
        return None


def word_count(text):
    return len(re.findall(r"\b[\w'’-]+\b", text or ""))
