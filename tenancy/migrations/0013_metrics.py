"""Metrics (spec 0006, slice 4): the daily partitioned ``obs_metric_point``
table and its first partitions, the series, the hourly rollups and the
watermarks.

Points have no foreign key to their series (a partitioned table would need
one per partition); purging a school deletes its points by series id.
"""
from datetime import timezone as dt_timezone

import django.contrib.postgres.fields
import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone

CREATE = """
CREATE TABLE public.obs_metric_point (
    series_id bigint NOT NULL,
    minute timestamptz NOT NULL,
    count bigint NOT NULL,
    sum double precision NOT NULL,
    min double precision NULL,
    max double precision NULL,
    last double precision NULL,
    buckets integer[] NULL,
    PRIMARY KEY (series_id, minute)
) PARTITION BY RANGE (minute);
"""

DROP = """
DROP TABLE IF EXISTS public.obs_metric_point;
"""


def first_partitions(apps, schema_editor):
    from tenancy.observability import partitions

    today = timezone.now().astimezone(dt_timezone.utc).date()
    with schema_editor.connection.cursor() as cursor:
        partitions.ensure(cursor, ["obs_metric_point"], today)


class Migration(migrations.Migration):

    dependencies = [
        ('tenancy', '0012_logs'),
    ]

    operations = [
        migrations.RunSQL(CREATE, DROP),
        migrations.RunPython(first_partitions, migrations.RunPython.noop),
        migrations.CreateModel(
            name='ObsMetricPoint',
            fields=[
                ('series_id', models.BigIntegerField(primary_key=True, serialize=False)),
                ('minute', models.DateTimeField()),
                ('count', models.BigIntegerField()),
                ('sum', models.FloatField()),
                ('min', models.FloatField(null=True)),
                ('max', models.FloatField(null=True)),
                ('last', models.FloatField(null=True)),
                ('buckets', django.contrib.postgres.fields.ArrayField(base_field=models.IntegerField(), null=True, size=None)),
            ],
            options={
                'db_table': 'obs_metric_point',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='MetricWatermark',
            fields=[
                ('name', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('minute', models.DateTimeField()),
            ],
        ),
        migrations.CreateModel(
            name='MetricSeries',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('metric_name', models.CharField(max_length=100)),
                ('labels', models.JSONField(default=dict)),
                ('labels_hash', models.CharField(max_length=40)),
                ('last_seen', models.DateTimeField()),
                ('school', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='tenancy.school')),
            ],
        ),
        migrations.CreateModel(
            name='MetricRollup',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('hour', models.DateTimeField()),
                ('count', models.BigIntegerField()),
                ('sum', models.FloatField()),
                ('min', models.FloatField(null=True)),
                ('max', models.FloatField(null=True)),
                ('last', models.FloatField(null=True)),
                ('buckets', django.contrib.postgres.fields.ArrayField(base_field=models.IntegerField(), null=True, size=None)),
                ('series', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rollups', to='tenancy.metricseries')),
            ],
        ),
        migrations.AddConstraint(
            model_name='metricseries',
            constraint=models.UniqueConstraint(fields=('metric_name', 'school', 'labels_hash'), name='metric_series_once', nulls_distinct=False),
        ),
        migrations.AddConstraint(
            model_name='metricrollup',
            constraint=models.UniqueConstraint(fields=('series', 'hour'), name='metric_rollup_once'),
        ),
    ]
