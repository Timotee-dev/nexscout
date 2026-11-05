from django.contrib import admin

from .models import ActivityLog, Job, LLMCall, OutreachTarget, Profile, ScoutSettings, SourceState


@admin.register(ScoutSettings)
class ScoutSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Collect", {"fields": ("keywords", "source_remoteok", "source_remotive", "source_wwr", "source_jobicy", "source_himalayas", "source_hn",
                                "source_careers", "career_companies", "discover_companies")}),
        ("Filter", {"fields": ("allowed_locations", "excluded_titles", "block_senior_titles", "max_years_required", "max_age_days")}),
        ("Claude", {"fields": ("llm_model", "min_score", "score_threshold", "daily_score_cap", "daily_draft_cap",
                               "daily_outreach_cap", "score_batch_size", "draft_batch_size")}),
        ("Tracking", {"fields": ("followup_days",)}),
        ("Autopilot", {"fields": ("autopilot_enabled", "autopilot_every_minutes", "autopilot_last_beat",
                                  "autopilot_last_cycle", "autopilot_last_summary")}),
        ("Answer sheet", {"fields": ("answers_confirmed", "ans_work_authorization", "ans_authorized_elsewhere",
                                     "ans_need_sponsorship", "ans_relocate", "ans_salary", "ans_salary_number",
                                     "ans_notice_period", "ans_years_experience", "ans_degree_completed",
                                     "ans_education", "ans_linkedin", "ans_consent")}),
        ("Applying by email", {"fields": ("tailored_cv", "send_enabled", "auto_send", "auto_send_min_score", "daily_send_cap",
                                          "auto_followup")}),
    )

    def has_add_permission(self, request):
        return not ScoutSettings.objects.exists()


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not Profile.objects.exists()


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "source", "score", "status", "passed_filter", "posted_at", "follow_up_on")
    list_filter = ("status", "source", "passed_filter", "open_to_nigeria")
    search_fields = ("title", "company", "description", "notes")
    readonly_fields = ("created_at", "updated_at", "fetched_at", "scored_at", "drafted_at", "status_changed_at")
    date_hierarchy = "posted_at"
    list_per_page = 50
    fieldsets = (
        ("Listing", {"fields": ("title", "company", "company_url", "location", "job_type", "salary",
                                "salary_min", "salary_max", "tags", "source", "external_id", "url",
                                "apply_url", "also_seen_on", "posted_at", "fetched_at")}),
        ("Description", {"classes": ("collapse",), "fields": ("description",)}),
        ("Filter", {"fields": ("passed_filter", "filter_reasons", "scam_flags", "company_key", "title_key")}),
        ("Claude score", {"fields": ("score", "score_reason", "matched_skills", "missing_skills",
                                     "seniority_guess", "open_to_nigeria", "ai_red_flags", "scored_at", "score_error")}),
        ("Draft", {"fields": ("chosen_project", "cover_note", "opener", "draft_warnings", "drafted_at")}),
        ("Applying", {"fields": ("applied_via", "applied_by", "form_submitted_at", "apply_proof", "apply_error",
                                 "ats", "apply_queue", "queued_at", "autofilled_at", "cv_summary",
                                 "cv_project_order", "cv_file", "cv_built_at")}),
        ("Email application", {"fields": ("apply_email", "apply_subject", "email_subject", "email_body",
                                          "email_sent_at", "email_sent_by", "email_error", "followup_email_sent_at")}),
        ("Tracking", {"fields": ("status", "status_changed_at", "applied_at", "follow_up_on",
                                 "followed_up_at", "notes", "created_at", "updated_at")}),
    )


@admin.register(OutreachTarget)
class OutreachTargetAdmin(admin.ModelAdmin):
    list_display = ("company_name", "contact_name", "channel", "status", "drafted_at", "sent_at")
    list_filter = ("status", "channel")
    search_fields = ("company_name", "contact_name", "notes")


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "message")
    list_filter = ("kind",)
    search_fields = ("message",)


@admin.register(SourceState)
class SourceStateAdmin(admin.ModelAdmin):
    list_display = ("key", "last_fetched_at", "last_ok", "last_count", "last_message")


@admin.register(LLMCall)
class LLMCallAdmin(admin.ModelAdmin):
    list_display = ("created_at", "purpose", "model", "items", "ok", "duration_ms", "error")
    list_filter = ("purpose", "ok")
