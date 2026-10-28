"""Local workers must avoid the process pool on platforms that spawn."""
from io import StringIO
from unittest import mock

from django.test import SimpleTestCase

from ems.management.commands.celery import Command


class CeleryCommandTests(SimpleTestCase):
    def worker_argv(self, platform, *args):
        command = Command(stdout=StringIO())
        with mock.patch("ems.management.commands.celery.sys.platform", platform):
            parser = command.create_parser("manage.py", "celery")
            options = vars(parser.parse_args(list(args)))
            with mock.patch("ems.management.commands.celery.app.start") as start:
                command.run_celery(**options)
        return start.call_args.args[0]

    def test_spawn_platforms_use_solo_and_consume_both_queues(self):
        for platform in ("darwin", "win32"):
            with self.subTest(platform=platform):
                argv = self.worker_argv(platform)
                self.assertEqual(argv[argv.index("--pool") + 1], "solo")
                self.assertEqual(argv[argv.index("-Q") + 1], "celery,observability")

    def test_linux_keeps_prefork(self):
        argv = self.worker_argv("linux")
        self.assertEqual(argv[argv.index("--pool") + 1], "prefork")

    def test_pool_can_be_overridden(self):
        argv = self.worker_argv("darwin", "--pool", "threads", "-c", "2")
        self.assertEqual(argv[argv.index("--pool") + 1], "threads")
        self.assertEqual(argv[argv.index("--concurrency") + 1], "2")

    def test_beat_has_no_worker_pool_arguments(self):
        self.assertEqual(self.worker_argv("darwin", "beat"), ["beat", "-l", "info"])

    def test_all_runs_separate_beat_and_cleans_it_up_on_worker_failure(self):
        with mock.patch("ems.management.commands.celery.subprocess.Popen") as popen, mock.patch(
            "ems.management.commands.celery.atexit.register"
        ), mock.patch("ems.management.commands.celery.app.start", side_effect=RuntimeError("stop")) as start:
            command = Command(stdout=StringIO())
            parser = command.create_parser("manage.py", "celery")
            options = vars(parser.parse_args(["all", "--pool", "solo"]))
            with self.assertRaisesMessage(RuntimeError, "stop"):
                command.run_celery(**options)
            self.assertIn("solo", start.call_args.args[0])
            self.assertIn("beat", popen.call_args.args[0])
            popen.return_value.terminate.assert_called_once()
