"""
Pluggable job sources.

To add a source:
  1. Create scout/services/sources/<name>.py with a JobSource subclass.
  2. Decorate it with @register.
  3. (Optional) add a `source_<key>` BooleanField to ScoutSettings to toggle it.
Only use official public feeds/APIs whose terms allow automated access.
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("scout")

REGISTRY = {}


def register(cls):
    REGISTRY[cls.key] = cls
    return cls


@dataclass
class RawJob:
    source: str
    external_id: str
    title: str
    company: str
    url: str
    description_html: str = ""
    description: str = ""
    apply_url: str = ""
    company_url: str = ""
    location: str = ""
    job_type: str = ""
    salary: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    tags: list = field(default_factory=list)
    posted_at: datetime | None = None


class SourceError(Exception):
    pass


class JobSource:
    key = "base"
    label = "Base"
    homepage = ""
    # Minimum time between fetches. Boards ask you not to hammer them.
    min_interval = timedelta(hours=1)
    # Courtesy pause between requests within one fetch.
    request_pause_seconds = 2.0

    def __init__(self, session=None):
        self.session = session or self.build_session()

    @staticmethod
    def build_session():
        s = requests.Session()
        s.headers.update({
            "User-Agent": settings.SCOUT_USER_AGENT,
            "Accept": "application/json, application/rss+xml, application/xml;q=0.9, */*;q=0.5",
        })
        return s

    def get(self, url, **kwargs):
        """GET with timeout, one polite retry on 429/503 honouring Retry-After."""
        kwargs.setdefault("timeout", settings.HTTP_TIMEOUT_SECONDS)
        for attempt in (1, 2):
            try:
                resp = self.session.get(url, **kwargs)
            except requests.RequestException as exc:
                raise SourceError(f"{self.label}: couldn't connect ({exc.__class__.__name__})") from exc
            if resp.status_code in (429, 503) and attempt == 1:
                wait = resp.headers.get("Retry-After", "30")
                wait = int(wait) if wait.isdigit() else 30
                if wait > 120:
                    raise SourceError(f"{self.label}: rate limited (retry after {wait}s); skipping this run")
                logger.info("%s rate-limited, waiting %ss", self.label, wait)
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise SourceError(f"{self.label}: HTTP {resp.status_code}")
            return resp
        raise SourceError(f"{self.label}: rate limited twice; skipping this run")

    def pause(self):
        time.sleep(self.request_pause_seconds)

    def fetch(self, keywords):
        """Return a list[RawJob]. `keywords` is a hint; matching is re-checked centrally."""
        raise NotImplementedError

    @classmethod
    def due(cls, state, now=None):
        now = now or timezone.now()
        return state.last_fetched_at is None or now - state.last_fetched_at >= cls.min_interval
