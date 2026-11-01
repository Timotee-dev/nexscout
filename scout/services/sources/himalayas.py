"""
Himalayas public API — https://himalayas.app/api
We use the search endpoint with country=Nigeria, which returns jobs open to Nigeria
(including worldwide jobs). Data refreshes daily, so we fetch at most every 12 hours.
Terms: link to the job on Himalayas, use applicationLink unchanged, don't re-publish.
"""
from datetime import timedelta

from ..text import html_to_text, parse_datetime_any
from .base import JobSource, RawJob, SourceError, register


@register
class HimalayasSource(JobSource):
    key = "himalayas"
    label = "Himalayas"
    homepage = "https://himalayas.app"
    api_url = "https://himalayas.app/jobs/api/search"
    queries = ["python", "django"]
    pages = 2
    min_interval = timedelta(hours=12)
    request_pause_seconds = 3.0

    def fetch(self, keywords):
        jobs, seen, first = [], set(), True
        for q in self.queries:
            for page in range(1, self.pages + 1):
                if not first:
                    self.pause()
                first = False
                resp = self.get(self.api_url, params={"q": q, "country": "Nigeria", "sort": "recent", "page": page})
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise SourceError("Himalayas: response was not JSON") from exc
                batch = self.parse(data)
                for job in batch:
                    if job.external_id not in seen:
                        seen.add(job.external_id)
                        jobs.append(job)
                if len(batch) < 20:
                    break
        return jobs

    @staticmethod
    def _names(value):
        out = []
        for v in value or []:
            if isinstance(v, dict):
                v = v.get("name") or v.get("country") or v.get("slug") or ""
            v = str(v).strip()
            if v:
                out.append(v)
        return out

    @staticmethod
    def _salary(item):
        lo, hi = item.get("minSalary"), item.get("maxSalary")
        if not (lo or hi):
            return "", None, None
        cur = item.get("currency") or "USD"
        period = (item.get("salaryPeriod") or "annual").lower()
        sym = "$" if cur == "USD" else f"{cur} "
        text = "–".join(f"{sym}{int(v):,}" for v in (lo, hi) if v)
        if period not in ("annual", "yearly"):
            text += f" / {period}"
        yearly = period in ("annual", "yearly") and cur == "USD"
        return text, int(lo) if lo and yearly else None, int(hi) if hi and yearly else None

    def parse(self, data):
        items = data.get("jobs", []) if isinstance(data, dict) else (data or [])
        jobs = []
        for item in items or []:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            countries = self._names(item.get("locationRestrictions"))
            location = ", ".join(countries) if countries else "Worldwide"
            if len(location) > 280:
                location = location[:277] + "..."
            salary, smin, smax = self._salary(item)
            desc_html = item.get("description") or item.get("excerpt") or ""
            company_slug = item.get("companySlug") or ""
            guid = str(item.get("guid") or item.get("applicationLink") or "")
            link = guid if guid.startswith("http") else item.get("applicationLink") or ""
            seniority = item.get("seniority")
            if isinstance(seniority, list):
                seniority = ", ".join(str(s) for s in seniority)
            emp = item.get("employmentType") or ""
            job_type = " · ".join(x for x in [str(emp), str(seniority or "")] if x)
            jobs.append(RawJob(
                source=self.key,
                external_id=guid or f"{company_slug}:{item['title']}",
                title=item["title"].strip(),
                company=(item.get("companyName") or "").strip(),
                company_url=f"https://himalayas.app/companies/{company_slug}" if company_slug else "",
                url=link,
                apply_url=item.get("applicationLink") or "",
                description_html=desc_html,
                description=html_to_text(desc_html),
                location=location,
                job_type=job_type[:80],
                salary=salary, salary_min=smin, salary_max=smax,
                tags=[str(c) for c in (item.get("categories") or [])][:10],
                posted_at=parse_datetime_any(item.get("pubDate")),
            ))
        return jobs
