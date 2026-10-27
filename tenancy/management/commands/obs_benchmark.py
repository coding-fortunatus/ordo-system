"""The tracing overhead check (spec 0006, AC-7).

Sends the same request to a running server many times and prints p50 and
p95 latency. Give two URLs to compare a server with tracing on against one
with ``OBSERVABILITY_TRACING_ENABLED=False``; requests alternate between
them so both see the same load and the same database state. Run it against
Daphne, not ``runserver``, since that is what production serves with.
"""
import logging
import statistics
import time

import httpx
from django.core.management.base import BaseCommand, CommandError

BUDGET_PERCENT = 5


def percentile(samples, pct):
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered)) - 1))
    return ordered[index]


class Command(BaseCommand):
    help = "Measure p50 and p95 latency of one API route, optionally on two servers."

    def add_arguments(self, parser):
        parser.add_argument("url", help="Full URL, e.g. http://localhost:8010/api/students/")
        parser.add_argument("--compare", help="A second URL (the same route, tracing off)")
        parser.add_argument("--host", help="Host header, e.g. demo.localhost")
        parser.add_argument("--token", help="A school API token")
        parser.add_argument("-n", "--requests", type=int, default=300)
        parser.add_argument("--warmup", type=int, default=20)

    def handle(self, *args, **options):
        # httpx logs every request at INFO.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        headers = {}
        if options["host"]:
            headers["Host"] = options["host"]
        if options["token"]:
            headers["Authorization"] = f"Token {options['token']}"
        urls = [options["url"]] + ([options["compare"]] if options["compare"] else [])
        samples = {url: [] for url in urls}

        with httpx.Client(headers=headers, timeout=30) as client:
            for i in range(options["warmup"] + options["requests"]):
                for url in urls:
                    started = time.perf_counter()
                    response = client.get(url)
                    elapsed = (time.perf_counter() - started) * 1000
                    if response.status_code >= 400:
                        raise CommandError(f"{url} answered {response.status_code}: {response.text[:200]}")
                    if i >= options["warmup"]:
                        samples[url].append(elapsed)

        results = {}
        for url in urls:
            values = samples[url]
            results[url] = (percentile(values, 50), percentile(values, 95), statistics.mean(values))
            p50, p95, mean = results[url]
            self.stdout.write(f"{url}\n  n={len(values)}  p50={p50:.1f} ms  p95={p95:.1f} ms  mean={mean:.1f} ms")

        if options["compare"]:
            on, off = results[urls[0]][1], results[urls[1]][1]
            change = (on - off) / off * 100
            verdict = "within" if change < BUDGET_PERCENT else "over"
            style = self.style.SUCCESS if change < BUDGET_PERCENT else self.style.ERROR
            self.stdout.write(style(f"p95 change {change:+.1f}%, {verdict} the {BUDGET_PERCENT}% budget"))
