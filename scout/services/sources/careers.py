"""
Company career sites through their applicant-tracking systems' official public job APIs:
  Greenhouse  https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
  Lever       https://api.lever.co/v0/postings/{company}?mode=json
  Ashby       https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true
The company list is in Settings (one "ats:company" per line). Companies that 404 are skipped and reported.
Every job links back to the company's own posting.
"""
import html as htmllib
from datetime import datetime, timedelta, timezone as dt_tz

from ..text import html_to_text, parse_datetime_any
from .base import JobSource, RawJob, SourceError, register


def _ms(value):
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=dt_tz.utc)
    except (TypeError, ValueError, OverflowError):
        return None


@register
class CareerSitesSource(JobSource):
    key = "careers"
    label = "Company career sites"
    homepage = "https://www.greenhouse.com/job-board"
    min_interval = timedelta(hours=6)
    request_pause_seconds = 1.0

    def __init__(self, session=None, companies=None):
        super().__init__(session)
        self.companies = companies
        self.problems = []

    def fetch(self, keywords):
        companies = self.companies
        if companies is None:
            from scout.models import ScoutSettings
            companies = ScoutSettings.get_solo().career_company_list
        jobs = []
        for i, (ats, slug) in enumerate(companies):
            if i:
                self.pause()
            try:
                jobs.extend(getattr(self, f"fetch_{ats}")(slug))
            except SourceError as exc:
                self.problems.append(f"{ats}:{slug} ({str(exc).split(': ')[-1]})")
        if companies and not jobs and len(self.problems) == len(companies):
            raise SourceError(f"{self.label}: no company boards could be read ({', '.join(self.problems[:5])})")
        return jobs

    # ---------------------------------------------------------------- Greenhouse
    def fetch_greenhouse(self, token):
        data = self.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"}).json()
        return self.parse_greenhouse(data, token)

    def parse_greenhouse(self, data, token):
        out = []
        company = token.replace("-", " ").title()
        for j in (data or {}).get("jobs", []) or []:
            if not isinstance(j, dict) or not j.get("title"):
                continue
            desc_html = htmllib.unescape(j.get("content") or "")
            depts = [d.get("name") for d in j.get("departments") or [] if isinstance(d, dict) and d.get("name")]
            out.append(RawJob(
                source=self.key, external_id=f"gh:{token}:{j.get('id')}", title=j["title"].strip(),
                company=(j.get("company_name") or company).strip(), url=j.get("absolute_url") or "",
                apply_url=j.get("absolute_url") or "", description_html=desc_html,
                description=html_to_text(desc_html), location=((j.get("location") or {}).get("name") or "").strip(),
                tags=depts[:5], posted_at=parse_datetime_any(j.get("first_published") or j.get("updated_at")),
            ))
        return out

    # ---------------------------------------------------------------- Lever
    def fetch_lever(self, slug):
        data = self.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}).json()
        return self.parse_lever(data, slug)

    def parse_lever(self, data, slug):
        out = []
        company = slug.replace("-", " ").title()
        for p in data if isinstance(data, list) else []:
            if not isinstance(p, dict) or not p.get("text"):
                continue
            cats = p.get("categories") or {}
            location = cats.get("location") or ", ".join(cats.get("allLocations") or [])
            wt = (p.get("workplaceType") or "").lower()
            if wt == "remote" and "remote" not in location.lower():
                location = f"Remote - {location}" if location else "Remote"
            elif wt in ("onsite", "on-site") and location:
                location = f"Onsite - {location}"
            parts = [p.get("openingPlain"), p.get("descriptionBodyPlain") or p.get("descriptionPlain")]
            for lst in p.get("lists") or []:
                parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content', ''))}")
            parts.append(p.get("additionalPlain"))
            text = "\n\n".join(x for x in parts if x)
            out.append(RawJob(
                source=self.key, external_id=f"lever:{slug}:{p.get('id')}", title=p["text"].strip(), company=company,
                url=p.get("hostedUrl") or "", apply_url=p.get("applyUrl") or "", description=text,
                description_html="", location=location.strip(), job_type=(cats.get("commitment") or "")[:80],
                tags=[t for t in [cats.get("team"), cats.get("department")] if t][:5],
                posted_at=_ms(p.get("createdAt")),
            ))
        return out

    # ---------------------------------------------------------------- Ashby
    def fetch_ashby(self, org):
        data = self.get(f"https://api.ashbyhq.com/posting-api/job-board/{org}",
                        params={"includeCompensation": "true"}).json()
        return self.parse_ashby(data, org)

    def parse_ashby(self, data, org):
        out = []
        company = org.replace("-", " ").title()
        for j in (data or {}).get("jobs", []) or []:
            if not isinstance(j, dict) or not j.get("title") or j.get("isListed") is False:
                continue
            location = (j.get("location") or "").strip()
            extra = [s.get("location") for s in j.get("secondaryLocations") or [] if isinstance(s, dict) and s.get("location")]
            if extra:
                location = ", ".join([location] + extra) if location else ", ".join(extra)
            if j.get("isRemote") and "remote" not in location.lower():
                location = f"Remote - {location}" if location else "Remote"
            comp = j.get("compensation") or {}
            salary = comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary") or ""
            desc_html = j.get("descriptionHtml") or ""
            out.append(RawJob(
                source=self.key, external_id=f"ashby:{org}:{j.get('id')}", title=j["title"].strip(), company=company,
                url=j.get("jobUrl") or "", apply_url=j.get("applyUrl") or j.get("jobUrl") or "",
                description_html=desc_html, description=j.get("descriptionPlain") or html_to_text(desc_html),
                location=location[:280], job_type=(j.get("employmentType") or "")[:80], salary=str(salary)[:200],
                tags=[t for t in [j.get("department"), j.get("team")] if t][:5],
                posted_at=parse_datetime_any(j.get("publishedAt")),
            ))
        return out
