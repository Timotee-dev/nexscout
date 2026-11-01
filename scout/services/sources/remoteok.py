"""
Remote OK public JSON API — https://remoteok.com/api
Their terms: link back to the Remote OK listing and credit Remote OK as the source.
We keep the Remote OK URL as the job's main link and show the source on every job.
"""
from datetime import timedelta

from ..text import html_to_text, parse_datetime_any
from .base import JobSource, RawJob, SourceError, register


@register
class RemoteOKSource(JobSource):
    key = "remoteok"
    label = "Remote OK"
    homepage = "https://remoteok.com"
    api_url = "https://remoteok.com/api"
    min_interval = timedelta(minutes=30)

    def fetch(self, keywords):
        resp = self.get(self.api_url)
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceError("Remote OK: response was not JSON") from exc
        return self.parse(data)

    def parse(self, data):
        jobs = []
        for item in data or []:
            # First element is a legal notice, not a job.
            if not isinstance(item, dict) or "position" not in item:
                continue
            salary_min = item.get("salary_min") or None
            salary_max = item.get("salary_max") or None
            salary = ""
            if salary_min or salary_max:
                lo = f"${int(salary_min):,}" if salary_min else ""
                hi = f"${int(salary_max):,}" if salary_max else ""
                salary = f"{lo}–{hi}".strip("–")
            desc_html = item.get("description") or ""
            jobs.append(RawJob(
                source=self.key,
                external_id=str(item.get("id") or item.get("slug")),
                title=(item.get("position") or "").strip(),
                company=(item.get("company") or "").strip(),
                url=item.get("url") or f"https://remoteok.com/remote-jobs/{item.get('slug', '')}",
                apply_url=item.get("apply_url") or "",
                description_html=desc_html,
                description=html_to_text(desc_html),
                location=(item.get("location") or "").strip(),
                salary=salary,
                salary_min=int(salary_min) if salary_min else None,
                salary_max=int(salary_max) if salary_max else None,
                tags=[str(t) for t in item.get("tags") or []],
                posted_at=parse_datetime_any(item.get("date") or item.get("epoch")),
            ))
        return jobs
