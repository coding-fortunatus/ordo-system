from django.db import migrations

KEY = "distribution"


def add_everywhere(apps, schema_editor):
    """Distribution was always on before it became a plan feature, so every
    plan and every school feature override gets it: no school loses it."""
    Plan = apps.get_model("tenancy", "Plan")
    School = apps.get_model("tenancy", "School")
    for plan in Plan.objects.all():
        if KEY not in plan.features:
            plan.features = sorted([*plan.features, KEY])
            plan.save(update_fields=["features"])
    for school in School.objects.exclude(features_override=None):
        if KEY not in school.features_override:
            school.features_override = sorted([*school.features_override, KEY])
            school.save(update_fields=["features_override"])


def remove_everywhere(apps, schema_editor):
    Plan = apps.get_model("tenancy", "Plan")
    School = apps.get_model("tenancy", "School")
    for plan in Plan.objects.all():
        plan.features = [f for f in plan.features if f != KEY]
        plan.save(update_fields=["features"])
    for school in School.objects.exclude(features_override=None):
        school.features_override = [f for f in school.features_override if f != KEY]
        school.save(update_fields=["features_override"])


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0015_demo_requests"),
    ]

    operations = [
        migrations.RunPython(add_everywhere, remove_everywhere),
    ]
