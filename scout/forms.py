from django import forms

from .models import Job, OutreachTarget, Profile, ScoutSettings


class SettingsForm(forms.ModelForm):
    class Meta:
        model = ScoutSettings
        exclude = ["updated_at"]
        widgets = {
            "keywords": forms.Textarea(attrs={"rows": 2}),
            "allowed_locations": forms.Textarea(attrs={"rows": 2}),
            "excluded_titles": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_score_batch_size(self):
        v = self.cleaned_data["score_batch_size"]
        if not 1 <= v <= 10:
            raise forms.ValidationError("Use 1–10 jobs per call.")
        return v

    def clean_draft_batch_size(self):
        v = self.cleaned_data["draft_batch_size"]
        if not 1 <= v <= 5:
            raise forms.ValidationError("Use 1–5 jobs per call.")
        return v

    def clean_min_score(self):
        v = self.cleaned_data["min_score"]
        if v > 100:
            raise forms.ValidationError("0–100.")
        return v

    def clean_score_threshold(self):
        v = self.cleaned_data["score_threshold"]
        if v > 100:
            raise forms.ValidationError("0–100.")
        return v


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["prompt_profile"]
        widgets = {"prompt_profile": forms.Textarea(attrs={"rows": 18, "spellcheck": "false"})}
        labels = {"prompt_profile": "Profile sent to Claude"}


class JobDraftForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = ["cover_note", "opener"]
        widgets = {
            "cover_note": forms.Textarea(attrs={"rows": 9, "data-wordcount": "150"}),
            "opener": forms.Textarea(attrs={"rows": 2}),
        }


class JobNotesForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = ["notes", "follow_up_on"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4, "placeholder": "Who you spoke to, links, salary talk…"}),
            "follow_up_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


class OutreachForm(forms.ModelForm):
    class Meta:
        model = OutreachTarget
        fields = ["company_name", "website", "contact_name", "contact_role", "linkedin_url",
                  "channel", "what_they_do", "angle", "notes"]
        widgets = {
            "what_they_do": forms.Textarea(attrs={"rows": 2, "placeholder": "e.g. Lagos fintech building payouts for SMEs"}),
            "angle": forms.TextInput(attrs={"placeholder": "e.g. I've built Paystack payouts end to end"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class OutreachMessageForm(forms.ModelForm):
    class Meta:
        model = OutreachTarget
        fields = ["message", "notes"]
        widgets = {"message": forms.Textarea(attrs={"rows": 8}), "notes": forms.Textarea(attrs={"rows": 3})}
