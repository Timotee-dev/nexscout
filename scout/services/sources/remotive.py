"""
Remotive public API — https://remotive.com/api/remote-jobs
Their guidance: don't call it more than a few times a day (listings are delayed ~24h anyway),
credit Remotive and link back to the listing. So: one request per run, at most every 6 hours.
"""
from datetime import timedelta

from ..text import html_to_text, parse_datetime_any
from .base import JobSource, RawJob, SourceError, register


@register
class RemotiveSource(JobSource):
    key = "remotive"
    label = "Remotive"
    homepage = "https://remotive.com"
    api_url = "https://remotive.com/api/remote-jobs"
    category = "software-dev"
    min_interval = timedelta(hours=6)

    def fetch(self, keywords):
        resp = self.get(self.api_url, params={"category": self.category})
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceError("Remotive: response was not JSON") from exc
        return self.parse(data)

    def parse(self, data):
        jobs = []
        for item in (data or {}).get("jobs", []):
            desc_html = item.get("description") or ""
            jobs.append(RawJob(
                source=self.key,
                external_id=str(item.get("id")),
                title=(item.get("title") or "").strip(),
                company=(item.get("company_name") or "").strip(),
                url=item.get("url") or "",
                description_html=desc_html,
                description=html_to_text(desc_html),
                location=(item.get("candidate_required_location") or "").strip(),
                job_type=(item.get("job_type") or "").replace("_", " "),
                salary=(item.get("salary") or "").strip(),
                tags=[str(t) for t in item.get("tags") or []],
                posted_at=parse_datetime_any(item.get("publication_date")),
            ))
        return jobs
