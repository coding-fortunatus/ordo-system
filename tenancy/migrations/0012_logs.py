"""Logs (spec 0006, slice 3): the daily partitioned ``obs_log`` table, its
first partitions, and the trigram index for text search (``pg_trgm`` came
with slice 1).

The id is a ``bigserial``: Postgres 16 (production) allows no identity
column on a partitioned table. The sequence also orders lines by when they
were flushed, which live log search pages by.
"""
from datetime import timezone as dt_timezone

from django.db import migrations, models
from django.utils import timezone

CREATE = """
CREATE TABLE public.obs_log (
    id bigserial NOT NULL,
    logged_at timestamptz NOT NULL,
    school_id integer NULL,
    level smallint NOT NULL,
    logger varchar(100) NOT NULL,
    message text NOT NULL,
    trace_id varchar(32) NULL,
    span_id varchar(16) NULL,
    request_id varchar(32) NOT NULL DEFAULT '',
    user_id integer NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (id, logged_at)
) PARTITION BY RANGE (logged_at);
CREATE INDEX obs_log_school_logged ON public.obs_log (school_id, logged_at DESC);
CREATE INDEX obs_log_trace ON public.obs_log (trace_id);
CREATE INDEX obs_log_request ON public.obs_log (request_id);
CREATE INDEX obs_log_message_trgm ON public.obs_log USING gin (message gin_trgm_ops);
"""

DROP = """
DROP TABLE IF EXISTS public.obs_log;
"""


def first_partitions(apps, schema_editor):
    from tenancy.observability import partitions

    today = timezone.now().astimezone(dt_timezone.utc).date()
    with schema_editor.connection.cursor() as cursor:
        partitions.ensure(cursor, ["obs_log"], today)


class Migration(migrations.Migration):

    dependencies = [
        ('tenancy', '0011_error_event_trace'),
    ]

    operations = [
        migrations.RunSQL(CREATE, DROP),
        migrations.RunPython(first_partitions, migrations.RunPython.noop),
        migrations.CreateModel(
            name='ObsLog',
            fields=[
                ('id', models.BigIntegerField(primary_key=True, serialize=False)),
                ('logged_at', models.DateTimeField()),
                ('level', models.SmallIntegerField()),
                ('logger', models.CharField(max_length=100)),
                ('message', models.TextField()),
                ('trace_id', models.CharField(max_length=32, null=True)),
                ('span_id', models.CharField(max_length=16, null=True)),
                ('request_id', models.CharField(blank=True, max_length=32)),
                ('user_id', models.IntegerField(null=True)),
                ('attributes', models.JSONField(default=dict)),
            ],
            options={
                'db_table': 'obs_log',
                'managed': False,
            },
        ),
    ]
