"""Traces (spec 0006, slice 1): the daily partitioned ``obs_segment`` and
``obs_span`` tables, their first partitions, and ``pg_trgm`` for log search.

Raw SQL, because Django 5.0 can't declare a partitioned table or its
composite key; the models are unmanaged. Every index is declared on the
parent, so Postgres builds it on each partition.
"""
from datetime import timezone as dt_timezone

from django.db import migrations, models
from django.utils import timezone

CREATE = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE public.obs_segment (
    root_span_id varchar(16) NOT NULL,
    trace_id varchar(32) NOT NULL,
    started_at timestamptz NOT NULL,
    school_id integer NULL,
    kind varchar(8) NOT NULL,
    root_name varchar(255) NOT NULL,
    route varchar(255) NOT NULL DEFAULT '',
    method varchar(8) NOT NULL DEFAULT '',
    status_code smallint NULL,
    duration_ms integer NOT NULL,
    is_error boolean NOT NULL,
    is_slow boolean NOT NULL,
    span_count integer NOT NULL,
    truncated boolean NOT NULL,
    user_id integer NULL,
    request_id varchar(32) NOT NULL DEFAULT '',
    release varchar(64) NOT NULL,
    process_role varchar(24) NOT NULL,
    PRIMARY KEY (root_span_id, started_at)
) PARTITION BY RANGE (started_at);
CREATE INDEX obs_segment_school_started ON public.obs_segment (school_id, started_at DESC);
CREATE INDEX obs_segment_errors ON public.obs_segment (started_at DESC) WHERE is_error;
CREATE INDEX obs_segment_route_started ON public.obs_segment (route, started_at DESC);
CREATE INDEX obs_segment_trace ON public.obs_segment (trace_id text_pattern_ops);

CREATE TABLE public.obs_span (
    span_id varchar(16) NOT NULL,
    trace_id varchar(32) NOT NULL,
    parent_span_id varchar(16) NULL,
    root_span_id varchar(16) NOT NULL,
    started_at timestamptz NOT NULL,
    school_id integer NULL,
    name varchar(255) NOT NULL,
    kind varchar(8) NOT NULL,
    duration_ms numeric(10, 2) NOT NULL,
    status varchar(5) NOT NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (span_id, started_at)
) PARTITION BY RANGE (started_at);
CREATE INDEX obs_span_trace ON public.obs_span (trace_id);
CREATE INDEX obs_span_school ON public.obs_span (school_id);
"""

DROP = """
DROP TABLE IF EXISTS public.obs_span;
DROP TABLE IF EXISTS public.obs_segment;
"""


def first_partitions(apps, schema_editor):
    from tenancy.observability import partitions

    today = timezone.now().astimezone(dt_timezone.utc).date()
    with schema_editor.connection.cursor() as cursor:
        partitions.ensure(cursor, ["obs_segment", "obs_span"], today)


class Migration(migrations.Migration):

    dependencies = [
        ('tenancy', '0009_portal_sync_feature'),
    ]

    operations = [
        migrations.RunSQL(CREATE, DROP),
        migrations.RunPython(first_partitions, migrations.RunPython.noop),
        migrations.CreateModel(
            name='ObsSegment',
            fields=[
                ('root_span_id', models.CharField(max_length=16, primary_key=True, serialize=False)),
                ('trace_id', models.CharField(max_length=32)),
                ('started_at', models.DateTimeField()),
                ('kind', models.CharField(max_length=8)),
                ('root_name', models.CharField(max_length=255)),
                ('route', models.CharField(blank=True, max_length=255)),
                ('method', models.CharField(blank=True, max_length=8)),
                ('status_code', models.SmallIntegerField(null=True)),
                ('duration_ms', models.IntegerField()),
                ('is_error', models.BooleanField()),
                ('is_slow', models.BooleanField()),
                ('span_count', models.IntegerField()),
                ('truncated', models.BooleanField()),
                ('user_id', models.IntegerField(null=True)),
                ('request_id', models.CharField(blank=True, max_length=32)),
                ('release', models.CharField(max_length=64)),
                ('process_role', models.CharField(max_length=24)),
            ],
            options={
                'db_table': 'obs_segment',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='ObsSpan',
            fields=[
                ('span_id', models.CharField(max_length=16, primary_key=True, serialize=False)),
                ('trace_id', models.CharField(max_length=32)),
                ('parent_span_id', models.CharField(max_length=16, null=True)),
                ('root_span_id', models.CharField(max_length=16)),
                ('started_at', models.DateTimeField()),
                ('name', models.CharField(max_length=255)),
                ('kind', models.CharField(max_length=8)),
                ('duration_ms', models.DecimalField(decimal_places=2, max_digits=10)),
                ('status', models.CharField(max_length=5)),
                ('attributes', models.JSONField(default=dict)),
            ],
            options={
                'db_table': 'obs_span',
                'managed': False,
            },
        ),
    ]
