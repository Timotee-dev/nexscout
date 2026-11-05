from datetime import timedelta

from django.db import models
from django.urls import reverse
from django.utils import timezone


def default_keywords():
    return "Django, Python, backend, DRF, full-stack Python"


def default_excluded_titles():
    from scout.services.filters import DEFAULT_EXCLUDED_TITLES
    return ", ".join(DEFAULT_EXCLUDED_TITLES)


DEFAULT_CAREER_COMPANIES = """greenhouse:gitlab
greenhouse:canonical
greenhouse:mozilla
greenhouse:wikimedia
greenhouse:grafanalabs
greenhouse:elastic
ashby:posthog
ashby:supabase
ashby:linear
ashby:railway"""


def default_career_companies():
    return DEFAULT_CAREER_COMPANIES


def default_locations():
    return "Worldwide, Anywhere, Global, Africa, EMEA, Nigeria, Lagos, West Africa"


class SingletonModel(models.Model):
    """One row only (pk=1)."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # never delete the singleton
        return

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ScoutSettings(SingletonModel):
    # Collect
    keywords = models.TextField(default=default_keywords, help_text="Comma-separated. A keyword matches if all its words appear in the job.")
    source_remoteok = models.BooleanField("Remote OK", default=True)
    source_remotive = models.BooleanField("Remotive", default=True)
    source_wwr = models.BooleanField("We Work Remotely", default=True)
    source_jobicy = models.BooleanField("Jobicy", default=True)
    source_himalayas = models.BooleanField("Himalayas", default=True)
    source_hn = models.BooleanField("HN Who is hiring", default=True)
    source_careers = models.BooleanField("Company career sites", default=True)
    career_companies = models.TextField(
        default=default_career_companies, blank=True,
        help_text="One per line as ats:company, e.g. greenhouse:gitlab, lever:acme, ashby:posthog.",
    )
    discover_companies = models.BooleanField(
        "Add companies automatically", default=True,
        help_text="When a job from another board links to a Greenhouse, Lever or Ashby page, watch that company too.",
    )

    # Filter
    allowed_locations = models.TextField(default=default_locations, help_text="Comma-separated locations that count as open to you.")
    block_senior_titles = models.BooleanField(default=True, help_text="Drop Senior / Staff / Lead / Principal titles.")
    max_years_required = models.PositiveSmallIntegerField(default=5, help_text="Drop jobs asking for this many years or more. 0 disables.")
    max_age_days = models.PositiveSmallIntegerField(default=14)
    excluded_titles = models.TextField(
        default=default_excluded_titles, blank=True,
        help_text="Comma-separated. Jobs whose title contains any of these are skipped.",
    )

    # Claude
    llm_model = models.CharField(max_length=80, blank=True, help_text="Leave blank to use LLM_MODEL from .env.")
    min_score = models.PositiveSmallIntegerField(
        "Minimum score to keep", default=50,
        help_text="Jobs Claude scores below this move to Filtered out. 0 keeps everything.",
    )
    score_threshold = models.PositiveSmallIntegerField(
        "Draft threshold", default=65, help_text="Jobs at or above this get a cover note drafted automatically.",
    )
    daily_score_cap = models.PositiveSmallIntegerField(default=40, help_text="Max jobs scored per day.")
    daily_draft_cap = models.PositiveSmallIntegerField(default=10, help_text="Max application drafts per day.")
    daily_outreach_cap = models.PositiveSmallIntegerField(default=10, help_text="Max outreach messages drafted per day.")
    score_batch_size = models.PositiveSmallIntegerField(default=5, help_text="Jobs per Claude call when scoring.")
    draft_batch_size = models.PositiveSmallIntegerField(default=3, help_text="Jobs per Claude call when drafting.")

    # Tracking
    followup_days = models.PositiveSmallIntegerField(default=7)

    # Autopilot
    autopilot_enabled = models.BooleanField(
        "Autopilot on", default=True,
        help_text="While the autopilot window is running, NexScout fetches, scores and drafts on its own.",
    )
    autopilot_every_minutes = models.PositiveSmallIntegerField(
        "Run every (minutes)", default=60,
        help_text="How often autopilot runs a cycle. Each job board still keeps its own polite gap.",
    )
    autopilot_last_beat = models.DateTimeField(null=True, blank=True)
    autopilot_last_cycle = models.DateTimeField(null=True, blank=True)
    autopilot_last_summary = models.CharField(max_length=300, blank=True)

    # Applying by email
    send_enabled = models.BooleanField(
        "Allow sending applications", default=True,
        help_text="Shows Send buttons for jobs that take applications by email. Needs Gmail set up in .env.",
    )
    auto_send = models.BooleanField(
        "Send automatically", default=False,
        help_text="Autopilot sends email applications without asking, but only for jobs that pass every safety check.",
    )
    auto_send_min_score = models.PositiveSmallIntegerField("Auto-send only at score", default=75)
    daily_send_cap = models.PositiveSmallIntegerField("Max applications sent per day", default=5)
    tailored_cv = models.BooleanField(
        "Tailored CV for each job", default=True,
        help_text="Builds a PDF CV for each job that leads with the most relevant projects and skills (facts from your CV only).",
    )
    auto_followup = models.BooleanField(
        "Send follow-ups automatically", default=False,
        help_text="When a follow-up is due, autopilot sends a short, polite follow-up email.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Scout settings"
        verbose_name_plural = "Scout settings"

    def __str__(self):
        return "Scout settings"

    @property
    def keyword_list(self):
        return [k.strip() for k in self.keywords.split(",") if k.strip()]

    @property
    def excluded_title_list(self):
        return [k.strip() for k in self.excluded_titles.split(",") if k.strip()]

    @property
    def career_company_list(self):
        out = []
        for line in (self.career_companies or "").replace(",", "\n").splitlines():
            ats, _, slug = line.strip().partition(":")
            ats, slug = ats.strip().lower(), slug.strip()
            if ats in ("greenhouse", "lever", "ashby") and slug and (ats, slug) not in out:
                out.append((ats, slug))
        return out

    def add_career_company(self, ats, slug, save=True):
        if (ats, slug) in self.career_company_list or len(self.career_company_list) >= 300:
            return False
        self.career_companies = (self.career_companies.rstrip() + f"\n{ats}:{slug}").strip()
        if save:
            self.save(update_fields=["career_companies", "updated_at"])
        return True

    @property
    def location_list(self):
        return [k.strip() for k in self.allowed_locations.split(",") if k.strip()]

    @property
    def model_name(self):
        from django.conf import settings
        return self.llm_model.strip() or settings.LLM_MODEL

    def source_enabled(self, key):
        return bool(getattr(self, f"source_{key}", False))


class Profile(SingletonModel):
    """Your candidate profile. Seeded from the CV; everything editable."""

    full_name = models.CharField(max_length=120, blank=True)
    headline = models.CharField(max_length=200, blank=True)
    location_line = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    github = models.URLField(blank=True)
    portfolio = models.URLField(blank=True)
    summary = models.TextField(blank=True)
    skills = models.TextField(blank=True, help_text="One line per area, e.g. 'Backend: Python, Django'")
    projects = models.JSONField(default=list, blank=True, help_text="List of {key, name, stack, one_liner, facts[], tags[]}")
    availability = models.TextField(blank=True)
    looking_for = models.TextField(blank=True)
    prompt_profile = models.TextField(
        blank=True,
        help_text="The exact profile text sent to Claude. Auto-built from the CV; edit freely.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.full_name or "Profile"

    @classmethod
    def get_solo(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        if created or not obj.full_name:
            from scout.services.profile_seed import apply_cv_seed
            apply_cv_seed(obj)
        return obj

    def project_by_key(self, key):
        for p in self.projects or []:
            if p.get("key") == key:
                return p
        return None

    @property
    def project_names(self):
        return [p.get("name") for p in self.projects or []]


class Job(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        SHORTLISTED = "shortlisted", "Shortlisted"
        APPLIED = "applied", "Applied"
        INTERVIEWING = "interviewing", "Interviewing"
        OFFER = "offer", "Offer"
        REJECTED = "rejected", "Rejected"
        GHOSTED = "ghosted", "Ghosted"
        SKIPPED = "skipped", "Skipped"

    class NigeriaFit(models.TextChoices):
        YES = "yes", "Yes"
        NO = "no", "No"
        UNCLEAR = "unclear", "Unclear"

    # Identity
    source = models.CharField(max_length=40, db_index=True)
    external_id = models.CharField(max_length=200)
    url = models.URLField(max_length=1000)
    apply_url = models.URLField(max_length=1000, blank=True)
    also_seen_on = models.JSONField(default=list, blank=True, help_text="Other sources that listed the same job.")

    # Listing
    title = models.CharField(max_length=300)
    company = models.CharField(max_length=200, blank=True)
    company_url = models.URLField(max_length=500, blank=True)
    location = models.CharField(max_length=300, blank=True)
    job_type = models.CharField(max_length=80, blank=True)
    salary = models.CharField(max_length=200, blank=True)
    salary_min = models.PositiveIntegerField(null=True, blank=True)
    salary_max = models.PositiveIntegerField(null=True, blank=True)
    tags = models.JSONField(default=list, blank=True)
    description = models.TextField(blank=True, help_text="Plain text")
    description_html = models.TextField(blank=True)
    posted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    fetched_at = models.DateTimeField(default=timezone.now, db_index=True)

    # Dedupe
    company_key = models.CharField(max_length=200, blank=True, db_index=True)
    title_key = models.CharField(max_length=300, blank=True)

    # Rule-based filter
    passed_filter = models.BooleanField(default=False, db_index=True)
    filter_reasons = models.JSONField(default=list, blank=True)
    scam_flags = models.JSONField(default=list, blank=True)

    # Claude scoring
    score = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    score_reason = models.CharField(max_length=400, blank=True)
    matched_skills = models.JSONField(default=list, blank=True)
    missing_skills = models.JSONField(default=list, blank=True)
    seniority_guess = models.CharField(max_length=40, blank=True)
    open_to_nigeria = models.CharField(max_length=10, choices=NigeriaFit.choices, blank=True)
    ai_red_flags = models.JSONField(default=list, blank=True)
    scored_at = models.DateTimeField(null=True, blank=True, db_index=True)
    score_error = models.CharField(max_length=300, blank=True)

    # Drafts
    chosen_project = models.CharField(max_length=80, blank=True)
    cover_note = models.TextField(blank=True)
    opener = models.TextField(blank=True)
    draft_warnings = models.JSONField(default=list, blank=True)
    drafted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Tracking
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    status_changed_at = models.DateTimeField(default=timezone.now)
    applied_at = models.DateTimeField(null=True, blank=True)
    follow_up_on = models.DateField(null=True, blank=True, db_index=True)
    followed_up_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    # Applying by email
    apply_email = models.EmailField(blank=True, help_text="Where the listing asks you to email your application.")
    apply_subject = models.CharField(max_length=300, blank=True, help_text="Subject line the listing asks for, if any.")
    email_subject = models.CharField(max_length=300, blank=True)
    email_body = models.TextField(blank=True)
    email_sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    email_sent_by = models.CharField(max_length=20, blank=True)  # "you" or "autopilot"
    email_error = models.CharField(max_length=300, blank=True)
    followup_email_sent_at = models.DateTimeField(null=True, blank=True)

    # Applying: queue, ATS and tailored CV
    class ApplyQueue(models.TextChoices):
        NONE = "", "-"
        QUEUED = "queued", "Queued: waiting for draft"
        REVIEW = "review", "Needs your review"
        FORM = "form", "Ready to autofill"
        FILLED = "filled", "Form filled, waiting for you"

    ats = models.CharField(max_length=20, blank=True, help_text="greenhouse / lever / ashby / workable / other")
    apply_queue = models.CharField(max_length=10, choices=ApplyQueue.choices, blank=True, db_index=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    autofilled_at = models.DateTimeField(null=True, blank=True)
    cv_summary = models.TextField(blank=True, help_text="Summary used on this job's tailored CV.")
    cv_project_order = models.JSONField(default=list, blank=True)
    cv_file = models.CharField(max_length=500, blank=True)
    cv_built_at = models.DateTimeField(null=True, blank=True)
    apply_error = models.CharField(max_length=300, blank=True, help_text="Why the last apply attempt didn't finish.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-score", "-posted_at"]
        constraints = [
            models.UniqueConstraint(fields=["source", "external_id"], name="uniq_job_per_source"),
        ]

    def __str__(self):
        return f"{self.title} @ {self.company}"

    def get_absolute_url(self):
        return reverse("scout:job_detail", args=[self.pk])

    @property
    def listing_url(self):
        return self.url or self.apply_url

    @property
    def applies_by_email(self):
        return bool(self.apply_email)

    @property
    def has_scam_risk(self):
        return bool(self.scam_flags or self.ai_red_flags)

    @property
    def is_followup_due(self):
        return (
            self.status == self.Status.APPLIED
            and self.follow_up_on is not None
            and self.follow_up_on <= timezone.localdate()
        )

    def set_status(self, new_status, followup_days=None, save=True):
        """Change status with side effects. Applied => follow-up N days later."""
        if new_status not in self.Status.values:
            raise ValueError(f"Unknown status: {new_status}")
        old = self.status
        if new_status == old:
            return old
        now = timezone.now()
        self.status = new_status
        self.status_changed_at = now
        if new_status == self.Status.APPLIED:
            if followup_days is None:
                followup_days = ScoutSettings.get_solo().followup_days
            self.applied_at = self.applied_at or now
            self.follow_up_on = timezone.localdate() + timedelta(days=followup_days)
        elif new_status != self.Status.APPLIED:
            # follow-ups only make sense while waiting on an application
            self.follow_up_on = None
        if save:
            self.save()
        return old


class OutreachTarget(models.Model):
    class Status(models.TextChoices):
        TO_DRAFT = "to_draft", "To draft"
        DRAFTED = "drafted", "Drafted"
        SENT = "sent", "Sent (by me)"
        REPLIED = "replied", "Replied"
        CLOSED = "closed", "Closed"

    company_name = models.CharField(max_length=200)
    website = models.URLField(max_length=500, blank=True)
    contact_name = models.CharField(max_length=150, blank=True)
    contact_role = models.CharField(max_length=150, blank=True)
    linkedin_url = models.URLField(max_length=500, blank=True, help_text="Pasted by you. NexScout never visits it.")
    what_they_do = models.TextField(blank=True, help_text="A line or two in your own words. Helps Claude be specific.")
    angle = models.CharField(max_length=300, blank=True, help_text="Optional: why you, why them.")
    channel = models.CharField(max_length=20, default="linkedin", choices=[("linkedin", "LinkedIn"), ("email", "Email")])

    message = models.TextField(blank=True)
    chosen_project = models.CharField(max_length=80, blank=True)
    drafted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    draft_error = models.CharField(max_length=300, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TO_DRAFT, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "-created_at"]

    def __str__(self):
        return self.company_name


class SourceState(models.Model):
    """Per-source bookkeeping so we respect each board's rate limits."""

    key = models.CharField(max_length=40, unique=True)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    last_ok = models.BooleanField(default=True)
    last_message = models.CharField(max_length=300, blank=True)
    last_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.key


class ActivityLog(models.Model):
    class Kind(models.TextChoices):
        FETCH = "fetch", "Fetch"
        FILTER = "filter", "Filter"
        SCORE = "score", "Score"
        DRAFT = "draft", "Draft"
        OUTREACH = "outreach", "Outreach"
        STATUS = "status", "Status"
        APPLY = "apply", "Application"
        RUN = "run", "Run"
        SYSTEM = "system", "System"
        ERROR = "error", "Error"

    kind = models.CharField(max_length=20, choices=Kind.choices, db_index=True)
    message = models.CharField(max_length=500)
    details = models.JSONField(default=dict, blank=True)
    job = models.ForeignKey(Job, null=True, blank=True, on_delete=models.SET_NULL, related_name="activity")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.kind}] {self.message}"


class LLMCall(models.Model):
    """One Claude CLI invocation. Lets you see exactly how much you're using."""

    purpose = models.CharField(max_length=20)  # score / draft / outreach / ping
    model = models.CharField(max_length=80)
    items = models.PositiveSmallIntegerField(default=1)
    ok = models.BooleanField(default=True)
    duration_ms = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
