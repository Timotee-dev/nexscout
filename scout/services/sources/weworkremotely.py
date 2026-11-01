"""
We Work Remotely public RSS feeds — https://weworkremotely.com/remote-job-rss-feed
Two category feeds that fit backend Python work. Links point back to WWR.
"""
import xml.etree.ElementTree as ET
from datetime import timedelta

from ..text import html_to_text, parse_datetime_any
from .base import JobSource, RawJob, SourceError, register


@register
class WeWorkRemotelySource(JobSource):
    key = "wwr"
    label = "We Work Remotely"
    homepage = "https://weworkremotely.com"
    feeds = [
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    ]
    min_interval = timedelta(minutes=30)

    def fetch(self, keywords):
        jobs = []
        for i, feed in enumerate(self.feeds):
            if i:
                self.pause()
            resp = self.get(feed)
            jobs.extend(self.parse(resp.content))
        return jobs

    @staticmethod
    def _child(item, name):
        el = item.find(name)
        if el is None:
            # tolerate namespaced tags
            for child in item:
                if child.tag.split("}")[-1] == name:
                    el = child
                    break
        return (el.text or "").strip() if el is not None and el.text else ""

    def parse(self, xml_bytes):
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise SourceError(f"We Work Remotely: bad RSS ({exc})") from exc
        jobs = []
        for item in root.iter("item"):
            raw_title = self._child(item, "title")
            company, _, title = raw_title.partition(":")
            if not title:  # no "Company: Title" pattern
                company, title = "", raw_title
            link = self._child(item, "link")
            guid = self._child(item, "guid") or link
            desc_html = self._child(item, "description")
            jobs.append(RawJob(
                source=self.key,
                external_id=guid,
                title=title.strip(),
                company=company.strip(),
                url=link,
                description_html=desc_html,
                description=html_to_text(desc_html),
                location=self._child(item, "region"),
                job_type=self._child(item, "type"),
                tags=[t for t in [self._child(item, "category")] if t],
                posted_at=parse_datetime_any(self._child(item, "pubDate")),
            ))
        return jobs
