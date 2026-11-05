"""Raise limits so autopilot can apply to ~20 jobs a day. Only changes values still at the old defaults."""
from django.db import migrations

OLD_TO_NEW = {
    "daily_send_cap": (5, 20),
    "auto_send_min_score": (75, 60),
    "daily_score_cap": (40, 120),
    "daily_draft_cap": (10, 30),
    "score_threshold": (65, 60),
    "auto_send": (False, True),
    "auto_followup": (False, True),
}


def forwards(apps, schema_editor):
    S = apps.get_model("scout", "ScoutSettings")
    for s in S.objects.all():
        for field, (old, new) in OLD_TO_NEW.items():
            if getattr(s, field) == old:
                setattr(s, field, new)
        s.save()


class Migration(migrations.Migration):
    dependencies = [("scout", "0008_hands_free_applying")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
