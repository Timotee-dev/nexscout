"""
Jobicy public API — https://jobicy.com/api/v2/remote-jobs  (docs: github.com/Jobicy/remote-jobs-api)
Terms: no more than one automated poll per hour (we use 3h), keep Jobicy as the source and keep its URL.
"""
from datetime import timedelta

from ..text import html_to_text, parse_datetime_any
from .base import JobSource, RawJob, SourceError, register


@register
class JobicySource(JobSource):
    key = "jobicy"
    label = "Jobicy"
    homepage = "https://jobicy.com"
    api_url = "https://jobicy.com/api/v2/remote-jobs"
    queries = ["python", "django"]
    min_interval = timedelta(hours=3)

    def fetch(self, keywords):
        jobs, seen = [], set()
        for i, tag in enumerate(self.queries):
            if i:
                self.pause()
            resp = self.get(self.api_url, params={"count": 50, "tag": tag})
            try:
                data = resp.json()
            except ValueError as exc:
                raise SourceError("Jobicy: response was not JSON") from exc
            for job in self.parse(data):
                if job.external_id not in seen:
                    seen.add(job.external_id)
                    jobs.append(job)
        return jobs

    @staticmethod
    def _salary(item):
        lo, hi = item.get("salaryMin"), item.get("salaryMax")
        if not (lo or hi):
            return "", None, None
        cur = item.get("salaryCurrency") or "USD"
        period = item.get("salaryPeriod") or "yearly"
        sym = "$" if cur == "USD" else f"{cur} "
        text = "–".join(f"{sym}{int(v):,}" for v in (lo, hi) if v)
        if period and period not in ("yearly", "annual"):
            text += f" / {period}"
        yearly = period in ("yearly", "annual") and cur == "USD"
        return text, int(lo) if lo and yearly else None, int(hi) if hi and yearly else None

    def parse(self, data):
        jobs = []
        for item in (data or {}).get("jobs", []) or []:
            if not isinstance(item, dict) or not item.get("jobTitle"):
                continue
            salary, smin, smax = self._salary(item)
            desc_html = item.get("jobDescription") or item.get("jobExcerpt") or ""
            level = (item.get("jobLevel") or "").strip()
            types = item.get("jobType") or []
            if isinstance(types, str):
                types = [types]
            job_type = ", ".join(t.replace("-", " ") for t in types)
            if level and level.lower() != "any":
                job_type = f"{job_type} · {level}" if job_type else level
            industries = item.get("jobIndustry") or []
            if isinstance(industries, str):
                industries = [industries]
            jobs.append(RawJob(
                source=self.key,
                external_id=str(item.get("id") or item.get("jobSlug")),
                title=item["jobTitle"].strip(),
                company=(item.get("companyName") or "").strip(),
                url=item.get("url") or "",
                description_html=desc_html,
                description=html_to_text(desc_html),
                location=(item.get("jobGeo") or "").strip(),
                job_type=job_type[:80],
                salary=salary, salary_min=smin, salary_max=smax,
                tags=[str(t) for t in industries][:10],
                posted_at=parse_datetime_any(item.get("pubDate")),
            ))
        return jobs
