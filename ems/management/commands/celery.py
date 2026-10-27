import atexit
import subprocess
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import autoreload

import redis

from core.celery import app


class Command(BaseCommand):
    help = 'Start Celery for local development (production uses docker-entrypoint.sh)'

    def add_arguments(self, parser):
        parser.add_argument(
            'mode',
            nargs='?',
            default='worker',
            choices=['worker', 'beat', 'all'],
            help="worker (default), beat, or all (worker with embedded beat)",
        )
        parser.add_argument('-l', '--loglevel', default='info')
        parser.add_argument('-c', '--concurrency', type=int, default=4)
        parser.add_argument(
            '--reload',
            action='store_true',
            help='Restart Celery when Python files change',
        )

    def handle(self, *args, **options):
        self.check_broker()

        if options['reload']:
            autoreload.run_with_reloader(self.run_celery, **options)
        else:
            self.run_celery(**options)

    def check_broker(self):
        broker_url = settings.CELERY_BROKER_URL
        try:
            redis.from_url(broker_url).ping()
        except (redis.ConnectionError, redis.TimeoutError):
            raise CommandError(
                f"Redis is not reachable at {broker_url}. "
                "Start it with 'sudo systemctl start redis-server'."
            )

    def run_celery(self, **options):
        mode = options['mode']
        loglevel = options['loglevel']
        concurrency = str(options['concurrency'])

        if mode == 'beat':
            argv = ['beat', '-l', loglevel]
        else:
            # Both queues locally; production gives observability its own
            # worker (spec 0006).
            argv = ['worker', '-l', loglevel, '--concurrency', concurrency,
                    '-Q', 'celery,observability']

        self.stdout.write(self.style.SUCCESS(f"Starting Celery {mode}..."))
        if mode == 'all':
            # Beat as its own process, as in production, not the worker's -B:
            # where processes spawn (macOS) Celery 5.3 pickles the embedded
            # beat with its arguments out of order and it dies with
            # "'CeleryApp' object is not callable".
            beat = subprocess.Popen([
                sys.executable, '-m', 'celery', '-A', 'core',
                'beat', '-l', loglevel,
            ])
            # The reloader exits the process on a change; never orphan beat.
            atexit.register(beat.terminate)
            try:
                app.start(argv)
            finally:
                beat.terminate()
        else:
            app.start(argv)
