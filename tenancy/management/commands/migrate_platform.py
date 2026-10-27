"""The deploy's one shot migrate step (``SERVICE_TYPE=migrate``).

Shared migrations, then every live school's, then the public domains, one
after another under a Postgres advisory lock, stopping at the first error.
Web, worker and beat only start once this exits 0, so no school is left on a
different schema version than the code (spec 0003, AC-8).
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import DEFAULT_DB_ALIAS, connections

LOCK_KEY = "ordo:migrate"


class Command(BaseCommand):
    help = "Migrate the public schema and every live school, under a lock."

    def handle(self, *args, **options):
        verbosity = options["verbosity"]
        # The lock lives on its own connection: migrate_schemas closes the
        # default one after each school, which would release it.
        lock = connections.create_connection(DEFAULT_DB_ALIAS)
        try:
            with lock.cursor() as cursor:
                self.stdout.write("Waiting for the migrate lock...")
                cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", [LOCK_KEY])
                try:
                    call_command(
                        "migrate_schemas", shared=True, interactive=False, verbosity=verbosity
                    )
                    call_command(
                        "migrate_schemas", tenant=True, interactive=False, verbosity=verbosity
                    )
                    call_command("ensure_public_domains", verbosity=verbosity)
                    # Telemetry partitions for today and the next days, so a
                    # release never waits for the nightly run (spec 0006).
                    from tenancy.observability import partitions

                    partitions.maintain()
                finally:
                    cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", [LOCK_KEY])
        finally:
            lock.close()
        self.stdout.write(self.style.SUCCESS("Migrations applied."))
