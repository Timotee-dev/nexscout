from django.core.management.base import BaseCommand, CommandError

from scout.models import ScoutSettings
from scout.services.claude_client import ClaudeError, ask_claude, build_command


class Command(BaseCommand):
    help = "One tiny Claude call to confirm the Claude Code CLI + your Pro login work."

    def handle(self, *args, **opts):
        model = ScoutSettings.get_solo().model_name
        try:
            self.stdout.write("Command: " + " ".join(build_command(model)))
            reply = ask_claude('Reply with exactly this JSON and nothing else: {"ok": true}', purpose="ping", model=model)
        except ClaudeError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Claude answered ({model}): {reply.strip()[:200]}"))
