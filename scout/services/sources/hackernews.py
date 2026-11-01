"""
Hacker News "Ask HN: Who is hiring?" — via the public HN Search API by Algolia (hn.algolia.com/api).
One thread per month; each top-level comment is a company posting, usually:
    Company | Role | Location / REMOTE | Type | link
We read the newest thread every 6 hours. Links go back to the comment on news.ycombinator.com.
"""
import re
from datetime import timedelta

from ..text import html_to_text, parse_datetime_any
from .base import JobSource, RawJob, SourceError, register

ROLE_WORDS = re.compile(r"engineer|developer|programmer|swe\b|backend|back-end|full[- ]?stack|python|django|"
                        r"architect|cto|devops|founding|software", re.I)
LOC_WORDS = re.compile(r"remote|onsite|on-site|hybrid|worldwide|anywhere|\b(us|usa|uk|eu|emea|europe)\b|"
                       r"timezone|\butc|\bgmt|[A-Z][a-z]+, [A-Z]{2}\b", re.I)
TYPE_WORDS = re.compile(r"full[- ]?time|part[- ]?time|contract|intern|freelance", re.I)


@register
class HackerNewsHiringSource(JobSource):
    key = "hn"
    label = "HN Who is hiring"
    homepage = "https://news.ycombinator.com/submitted?id=whoishiring"
    search_url = "https://hn.algolia.com/api/v1/search_by_date"
    item_url = "https://hn.algolia.com/api/v1/items/{id}"
    min_interval = timedelta(hours=6)

    def fetch(self, keywords):
        resp = self.get(self.search_url, params={"tags": "story,author_whoishiring", "hitsPerPage": 10})
        try:
            hits = resp.json().get("hits", [])
        except ValueError as exc:
            raise SourceError("HN: search response was not JSON") from exc
        story = next((h for h in hits if "who is hiring" in (h.get("title") or "").lower()), None)
        if not story:
            raise SourceError("HN: couldn't find this month's 'Who is hiring?' thread")
        self.pause()
        resp = self.get(self.item_url.format(id=story["objectID"]))
        try:
            thread = resp.json()
        except ValueError as exc:
            raise SourceError("HN: thread response was not JSON") from exc
        return self.parse_thread(thread)

    @staticmethod
    def split_header(text_html):
        """First line/paragraph of the comment, split on '|'."""
        first = re.split(r"<p>|\n", text_html or "", maxsplit=1)[0]
        first = html_to_text(first)
        return [p.strip() for p in first.split("|") if p.strip()], first

    def parse_comment(self, c):
        text_html = c.get("text") or ""
        if not text_html or c.get("author") in (None, "") or len(text_html) < 80:
            return None
        parts, first = self.split_header(text_html)
        if len(parts) < 2:
            return None
        company = re.sub(r"\(.*?\)", "", parts[0]).strip()[:120]
        rest = parts[1:]
        title = next((p for p in rest if ROLE_WORDS.search(p) and not LOC_WORDS.fullmatch(p)), "")
        location = next((p for p in rest if LOC_WORDS.search(p) and p != title), "")
        job_type = next((p for p in rest if TYPE_WORDS.search(p) and p not in (title, location)), "")
        if not title:
            return None
        if len(title) > 160:
            title = title[:157] + "..."
        cid = str(c.get("id"))
        return RawJob(
            source=self.key,
            external_id=cid,
            title=title,
            company=company,
            url=f"https://news.ycombinator.com/item?id={cid}",
            description_html=text_html,
            description=html_to_text(text_html),
            location=location[:280],
            job_type=job_type[:80],
            tags=[p[:40] for p in rest if p not in (title, location, job_type) and not p.startswith("http")][:6]
            + ["hn-who-is-hiring"],
            posted_at=parse_datetime_any(c.get("created_at") or c.get("created_at_i")),
        )

    def parse_thread(self, thread):
        jobs = []
        for c in (thread or {}).get("children", []) or []:
            if isinstance(c, dict):
                job = self.parse_comment(c)
                if job:
                    jobs.append(job)
        return jobs
