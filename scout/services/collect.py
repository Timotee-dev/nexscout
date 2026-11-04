"""
COLLECT + FILTER stage. No Claude calls here.
"""
import time
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from scout.models import Job, ScoutSettings, SourceState

from .activity import log
from .dedupe import company_key, find_duplicate, title_key
from .filters import FilterConfig, below_min_score_reason, evaluate
from .apply_email import detect as detect_apply_email
from .ats import detect_ats
from .scam import scam_flags
from .sources import SourceError, get_sources

DEDUPE_WINDOW_DAYS = 60


def ingest(raws, cfg, now=None):
    """Store new jobs. Returns counters. Jobs that don't match any keyword are not stored."""
    now = now or timezone.now()
    stats = {"seen": 0, "irrelevant": 0, "duplicates": 0, "passed": 0, "rejected": 0, "discovered": []}
    for raw in raws:
        if not raw.external_id or not raw.title:
            continue
        if Job.objects.filter(source=raw.source, external_id=raw.external_id).exists():
            stats["seen"] += 1
            continue
        result = evaluate(raw, cfg, now=now)
        if not result.keyword_match:
            stats["irrelevant"] += 1
            continue

        ats, ats_slug = detect_ats(raw.apply_url, raw.url)
        if raw.source != "careers" and ats in ("greenhouse", "lever", "ashby") and ats_slug:
            if (ats, ats_slug) not in stats["discovered"]:
                stats["discovered"].append((ats, ats_slug))

        ck = company_key(raw.company)
        if ck:
            candidates = Job.objects.filter(company_key=ck, fetched_at__gte=now - timedelta(days=DEDUPE_WINDOW_DAYS))
            dup = find_duplicate(raw, candidates)
            if dup:
                seen = dup.also_seen_on or []
                if not any(s.get("source") == raw.source for s in seen) and dup.source != raw.source:
                    seen.append({"source": raw.source, "url": raw.url})
                    dup.also_seen_on = seen
                    dup.save(update_fields=["also_seen_on", "updated_at"])
                stats["duplicates"] += 1
                continue

        flags = scam_flags(raw.company, raw.title, raw.description, raw.salary_max, raw.salary)
        apply_to, apply_subj = detect_apply_email(raw.description_html, raw.description)
        try:
            with transaction.atomic():
                Job.objects.create(
                    source=raw.source, external_id=raw.external_id[:200], url=raw.url[:1000],
                    apply_url=(raw.apply_url or "")[:1000], title=raw.title[:300], company=raw.company[:200],
                    company_url=(raw.company_url or "")[:500], location=raw.location[:300],
                    job_type=raw.job_type[:80], salary=raw.salary[:200], salary_min=raw.salary_min,
                    salary_max=raw.salary_max, tags=raw.tags[:20], description=raw.description,
                    description_html=raw.description_html, posted_at=raw.posted_at, fetched_at=now,
                    company_key=ck[:200], title_key=title_key(raw.title)[:300],
                    passed_filter=result.passed, filter_reasons=result.reasons, scam_flags=flags,
                    status=Job.Status.NEW if result.passed else Job.Status.SKIPPED,
                    apply_email=apply_to[:254], apply_subject=apply_subj[:300], ats=(ats or ("email" if apply_to else ""))[:20],
                )
        except IntegrityError:
            stats["seen"] += 1
            continue
        stats["passed" if result.passed else "rejected"] += 1
    return stats


def fetch_all(force=False, only=None, deadline=None):
    """Fetch every enabled source that is due. Returns {source_key: stats|error}."""
    s = ScoutSettings.get_solo()
    cfg = FilterConfig.from_settings(s)
    results = {}
    sources = get_sources()
    for i, (key, cls) in enumerate(sources.items()):
        if only and key not in only:
            continue
        if deadline and time.monotonic() > deadline:
            results[key] = "skipped: time limit"
            break
        if not s.source_enabled(key):
            results[key] = "disabled"
            continue
        state, _ = SourceState.objects.get_or_create(key=key)
        if not force and not cls.due(state):
            nxt = state.last_fetched_at + cls.min_interval
            results[key] = f"not due (next after {timezone.localtime(nxt):%H:%M})"
            continue
        source = cls()
        if i:
            source.pause()
        try:
            raws = source.fetch(s.keyword_list)
        except SourceError as exc:
            # don't stamp last_fetched_at: a failed fetch may retry next run
            state.last_ok = False
            state.last_message = str(exc)[:300]
            state.save()
            log("error", str(exc), source=key)
            results[key] = f"error: {exc}"
            continue
        stats = ingest(raws, cfg)
        added = 0
        if s.discover_companies:
            for ats, slug in stats.pop("discovered", []):
                added += bool(s.add_career_company(ats, slug))
            if added:
                log("fetch", f"Now watching {added} more company career site{'s' if added != 1 else ''} found via {cls.label}")
        else:
            stats.pop("discovered", None)
        problems = getattr(source, "problems", [])
        state.last_fetched_at = timezone.now()
        state.last_ok = True
        state.last_count = len(raws)
        state.last_message = (f"{len(raws)} listed · {stats['passed']} kept · {stats['rejected']} filtered · "
                              f"{stats['duplicates']} dupes"
                              + (f" · not found: {', '.join(problems[:4])}" if problems else ""))[:300]
        state.save()
        log("fetch", f"{cls.label}: {len(raws)} listings → {stats['passed']} kept, {stats['rejected']} filtered out, "
                     f"{stats['duplicates']} duplicates, {stats['seen']} already seen", source=key, **stats)
        results[key] = stats
    return results


def refilter_unscored():
    """Re-run the rules on jobs you haven't acted on yet (after filter settings or rules change)."""
    s = ScoutSettings.get_solo()
    cfg = FilterConfig.from_settings(s)
    changed = 0
    # Untouched jobs (still New) and jobs the rules skipped. Jobs you moved yourself are left alone.
    from django.db.models import Q
    qs = Job.objects.filter(
        Q(status=Job.Status.NEW) | Q(status=Job.Status.SKIPPED, passed_filter=False)
    )
    for job in qs.iterator():
        if not job.apply_email:
            found, subj = detect_apply_email(job.description_html, job.description)
            if found:
                job.apply_email, job.apply_subject = found[:254], subj[:300]
                job.save(update_fields=["apply_email", "apply_subject", "updated_at"])
        result = evaluate(job, cfg)
        low = below_min_score_reason(job.score, s.min_score)
        if result.passed and low:
            result.passed, result.reasons = False, [low]
        new_status = Job.Status.NEW if result.passed else Job.Status.SKIPPED
        if job.passed_filter != result.passed or job.filter_reasons != result.reasons or job.status != new_status:
            job.passed_filter = result.passed
            job.filter_reasons = result.reasons
            job.status = new_status
            job.save(update_fields=["passed_filter", "filter_reasons", "status", "updated_at"])
            changed += 1
    log("filter", f"Re-checked jobs against your filters: {changed} changed")
    return changed
