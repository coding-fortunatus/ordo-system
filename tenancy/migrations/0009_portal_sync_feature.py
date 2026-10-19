from django.db import migrations


def add_to_platform_plan(apps, schema_editor):
    """The internal `platform` plan has every feature, so it gets
    `portal_sync` too (spec 0005). Other plans get it only when the owner
    switches it on."""
    Plan = apps.get_model("tenancy", "Plan")
    for plan in Plan.objects.filter(code="platform"):
        if "portal_sync" not in plan.features:
            plan.features = sorted([*plan.features, "portal_sync"])
            plan.save(update_fields=["features"])


def remove_from_platform_plan(apps, schema_editor):
    Plan = apps.get_model("tenancy", "Plan")
    for plan in Plan.objects.filter(code="platform"):
        plan.features = [f for f in plan.features if f != "portal_sync"]
        plan.save(update_fields=["features"])


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0008_error_observability"),
    ]

    operations = [
        migrations.RunPython(add_to_platform_plan, remove_from_platform_plan),
    ]
