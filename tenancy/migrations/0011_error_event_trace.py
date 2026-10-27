"""Spec 0006, slice 2: an error event remembers the trace it happened in.
Nullable, so nothing is backfilled."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenancy', '0010_traces'),
    ]

    operations = [
        migrations.AddField(
            model_name='errorevent',
            name='trace_id',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddIndex(
            model_name='errorevent',
            index=models.Index(fields=['trace_id'], name='error_event_trace'),
        ),
    ]
