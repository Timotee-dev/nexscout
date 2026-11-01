from django.core.management.base import BaseCommand

from scout.models import Profile, ScoutSettings
from scout.services.activity import log
from scout.services.profile_seed import apply_cv_seed


class Command(BaseCommand):
    help = "Create your profile from the CV seed (and default settings). --force overwrites edits."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Overwrite the existing profile with the CV seed.")

    def handle(self, *args, **opts):
        ScoutSettings.get_solo()
        existed = Profile.objects.filter(pk=1, full_name__gt="").exists()
        profile = Profile.get_solo()
        if existed and opts["force"]:
            apply_cv_seed(profile)
            log("system", "Profile re-seeded from CV (--force)")
            self.stdout.write(self.style.SUCCESS("Profile overwritten from CV seed."))
        elif existed:
            self.stdout.write("Profile already exists; left as-is. Use --force to overwrite.")
        else:
            log("system", "Profile seeded from CV")
            self.stdout.write(self.style.SUCCESS(f"Profile seeded for {profile.full_name}."))
        self.stdout.write(f"Projects: {', '.join(profile.project_names)}")
