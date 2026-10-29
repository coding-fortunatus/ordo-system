import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// How to use the Observability pages (spec 0006). Kept in step with
// docs/guides/observability.md; change both together.

const SECTIONS = [
  ["access", "Who can use it"],
  ["overview", "Overview"],
  ["school-tab", "School tab"],
  ["traces", "Traces"],
  ["logs", "Logs"],
  ["metrics", "Metrics"],
  ["dashboards", "Dashboards"],
  ["alerts", "Alerts"],
  ["tasks", "Common tasks"],
  ["troubleshooting", "Troubleshooting"],
  ["settings", "Settings"],
] as const;

type SectionId = (typeof SECTIONS)[number][0];

const BUILT_IN_METRICS: Array<[string, string, string]> = [
  ["http.server.requests", "counter", "API requests, by route, method, status class"],
  ["http.server.duration", "histogram", "Request duration, by route, method, status class"],
  ["db.queries", "histogram", "Database queries per request, by route"],
  ["db.query.duration", "histogram", "Query duration, by operation"],
  ["db.slow_queries", "counter", "Queries over 500 ms, by operation"],
  ["redis.duration", "histogram", "Redis call duration, by command"],
  ["celery.task.duration", "histogram", "Job duration, by task and outcome"],
  ["celery.task.failures", "counter", "Jobs that failed for good, by task"],
  ["celery.queue.depth", "gauge · platform", "Jobs waiting, by queue"],
  ["celery.queue.wait", "histogram", "Time a job waited to start, by queue and task"],
  ["host.cpu.percent", "gauge · platform", "Host CPU use"],
  ["host.memory.percent", "gauge · platform", "Host memory use"],
  ["host.disk.used_bytes", "gauge · platform", "Disk space used, by path"],
  ["host.disk.free_bytes", "gauge · platform", "Disk space free, by path"],
  ["db.database.bytes", "gauge · platform", "Postgres database size"],
  ["db.schema.bytes", "gauge", "A school's Postgres schema size"],
  ["media.bytes", "gauge", "A school's uploaded files"],
  ["browser.vitals", "histogram", "Web Vitals (LCP, INP, CLS × 1000), by page"],
  ["obs.dropped", "counter · platform", "Telemetry dropped before storage, by signal and reason"],
];

const BUSINESS_METRICS: Array<[string, string, string]> = [
  ["ordo.timetable.runs", "counter", "Generation runs, by kind and outcome"],
  ["ordo.timetable.duration", "histogram", "Generation run duration, by kind and outcome"],
  ["ordo.portal_sync.runs", "counter", "Portal sync runs, by outcome and preview"],
  ["ordo.portal_sync.records", "counter", "Portal records synced, by outcome"],
  ["ordo.auth.logins", "counter", "Sign ins, by outcome and channel"],
  ["ordo.users.daily_active", "gauge", "Users active in the day"],
  ["ordo.uploads", "counter", "Uploads, by kind"],
  ["ordo.uploads.bytes", "counter", "Uploaded bytes, by kind"],
  ["ordo.exports", "counter", "Exports, by kind"],
  ["ordo.exports.bytes", "counter", "Exported bytes, by kind"],
];

const EXAMPLE_RULES: Array<[string, string, string, string, string, string]> = [
  ["A school is slow", "http.server.duration", "p95", "none", "Above 2000 for 10 min", "Each school"],
  ["Server errors spike", "http.server.requests", "rate", "status_class = 5xx", "Above 0.5 for 5 min", "Each school"],
  ["Jobs piling up", "celery.queue.depth", "last", "none", "Above 50 for 10 min", "Platform total"],
  ["Disk filling", "host.disk.free_bytes", "last", "path = /", "Below 5 GB for 15 min", "Platform total"],
];

const SETTINGS: Array<[string, string, string]> = [
  ["OBSERVABILITY_ENABLED", "True", "Turns all observability on or off"],
  ["OBSERVABILITY_TRACING_ENABLED", "True", "Turns tracing on or off"],
  ["OBSERVABILITY_LOGS_ENABLED", "True", "Turns log capture on or off"],
  ["OBSERVABILITY_METRICS_ENABLED", "True", "Turns metrics on or off"],
  ["OBSERVABILITY_BUFFER_REDIS_URL", "OBSERVABILITY_REDIS_URL", "The telemetry Redis (redis-obs in production)"],
  ["OBS_PROCESS_ROLE", "unset", "web, worker, worker-observability or beat, set per service"],
  ["OBS_TRACE_SAMPLE_PERCENT", "10", "Share of normal traces kept"],
  ["OBS_SLOW_HTTP_MS", "1000", "Requests slower than this are always kept"],
  ["OBS_SLOW_JOB_MS", "30000", "Jobs slower than this are always kept"],
  ["OBS_TRACE_KEEP_DAYS", "3", "Trace retention"],
  ["OBS_LOG_KEEP_DAYS", "7", "Log retention"],
  ["OBS_POINT_KEEP_DAYS", "7", "Minute metric retention"],
  ["OBS_ROLLUP_KEEP_DAYS", "90", "Hourly metric retention"],
  ["OBS_BUFFER_MAX", "50000", "Items per buffer before new ones are dropped"],
  ["OBS_DISK_PATHS", "/", "Paths sampled for disk use"],
  ["PLATFORM_ALERT_EMAILS", "active Owners", "Alert email recipients"],
];

function Section({ id, eyebrow, title, children }: { id: SectionId; eyebrow: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-6 space-y-4" aria-labelledby={`${id}-title`}>
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{eyebrow}</p>
        <h2 id={`${id}-title`} className="mt-1 font-serif text-[1.35rem] leading-tight tracking-[-0.01em]">
          {title}
        </h2>
      </div>
      <div className="max-w-3xl space-y-3 text-[14.5px] leading-[1.65] [&_li]:mt-1.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5">
        {children}
      </div>
    </section>
  );
}

function Subheading({ children }: { children: ReactNode }) {
  return <h3 className="pt-2 text-[15px] font-semibold">{children}</h3>;
}

function Note({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-l-2 border-brand bg-brand-soft px-4 py-3 text-[14px]">
      <span className="font-semibold">{title}</span> {children}
    </div>
  );
}

function Code({ children }: { children: ReactNode }) {
  return <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-[12.5px] break-all">{children}</code>;
}

function GuideTable({ head, rows, code = [] }: { head: string[]; rows: string[][]; code?: number[] }) {
  return (
    <Card className="py-0">
      <CardContent className="overflow-x-auto px-0">
        <Table>
          <TableHeader>
            <TableRow>
              {head.map((h) => (
                <TableHead key={h}>{h}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.join("|")}>
                {row.map((cell, i) => (
                  <TableCell key={i} className="align-top whitespace-normal">
                    {code.includes(i) && cell !== "none" ? <Code>{cell}</Code> : cell}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

const pageLink = "font-medium underline decoration-brand decoration-2 underline-offset-4 hover:text-foreground";

export default function ConsoleObservabilityGuidePage() {
  return (
    <div className="space-y-10">
      <PageHeader
        section="Console · Observability"
        title="Guide"
        description="How to use the Observability pages: what each one shows, which traces and logs are kept, and how alert rules decide to email."
      />

      <nav aria-label="On this page" className="flex flex-wrap gap-2">
        {SECTIONS.map(([id, label]) => (
          <a
            key={id}
            href={`#${id}`}
            className="rounded-full border border-border px-3 py-1 text-[13px] text-muted-foreground transition-colors hover:border-brand hover:text-foreground"
          >
            {label}
          </a>
        ))}
      </nav>

      <div className="grid gap-3 sm:grid-cols-3">
        {[
          ["Metrics", "Numbers over time: requests per second, p95 latency, jobs waiting, disk used."],
          ["Traces", "The timed steps of one request, job or WebSocket message, with every database, Redis and outgoing call inside it."],
          ["Logs", "Ordo's own log lines, tagged with the school, request and trace each one came from."],
        ].map(([name, text]) => (
          <Card key={name}>
            <CardContent className="space-y-1">
              <p className="text-[15px] font-semibold">{name}</p>
              <p className="text-[13.5px] leading-[1.55] text-muted-foreground">{text}</p>
            </CardContent>
          </Card>
        ))}
      </div>
      <p className="max-w-3xl text-[14.5px] leading-[1.65] text-muted-foreground">
        All three are stored in Ordo's own Postgres. Nothing is sent to an outside service.
      </p>

      <Section id="access" eyebrow="Permissions" title="Who can use it">
        <GuideTable
          head={["Action", "Who"]}
          rows={[
            ["View every Observability page", "Any admin"],
            ["Create and edit dashboards", "Any admin"],
            ["Delete a dashboard", "Owners"],
            ["Create, edit, turn on or off, or delete alert rules", "Owners"],
          ]}
        />
        <p>
          Rule changes and dashboard deletes are written to the{" "}
          <Link to="/console/audit" className={pageLink}>Audit log</Link>. School users can't see any of this. When a
          request fails for them, the only thing they see is a short <strong>Reference</strong> code, the first 8
          characters of the trace id. See <a href="#find-error" className={pageLink}>Finding a user's error</a>.
        </p>
      </Section>

      <Section id="overview" eyebrow="Page" title="Overview">
        <p>
          The <Link to="/console/observability" className={pageLink}>Overview</Link> answers one question: is Ordo
          healthy right now? Pick a <strong>Range</strong> (last hour up to 90 days) and a <strong>School</strong> (all
          schools by default).
        </p>
        <Subheading>Traffic</Subheading>
        <ul>
          <li><strong>Requests</strong>: API requests per second.</li>
          <li><strong>Error rate</strong>: the share of requests that ended in a server error (5xx). 4xx responses don't count.</li>
          <li><strong>Latency, p95</strong>: 95% of requests finished faster than this.</li>
          <li><strong>Job wait, p95</strong>: how long a background job waited in its queue before it started.</li>
        </ul>
        <Subheading>Server</Subheading>
        <p>These are platform wide, so the school picker doesn't change them.</p>
        <ul>
          <li><strong>Jobs waiting</strong>: Celery queue depth.</li>
          <li><strong>CPU</strong> and <strong>Memory</strong>: host use, in percent.</li>
          <li><strong>Disk free</strong> and <strong>Disk used</strong>: for each monitored path.</li>
          <li><strong>Database size</strong>: the whole Postgres database.</li>
        </ul>
        <Subheading>Tables</Subheading>
        <ul>
          <li><strong>Slowest routes</strong>: p95 latency and request count for each API route. Click a route to open its traces.</li>
          <li><strong>Busiest schools</strong>: the schools with the most requests in the range.</li>
        </ul>
        <Note title="Shaded minutes.">
          A shaded band on a chart means some telemetry was dropped during those minutes, usually because the telemetry
          Redis was down or full. Treat the numbers there as incomplete.
        </Note>
      </Section>

      <Section id="school-tab" eyebrow="Page" title="School Observability tab">
        <p>
          On a school's page in <Link to="/console/schools" className={pageLink}>Schools</Link>, the{" "}
          <strong>Observability</strong> tab shows the same kind of charts for that school only, plus its business numbers.
        </p>
        <GuideTable
          head={["Section", "Charts"]}
          rows={[
            ["Traffic", "Requests, Error rate, Latency p95, Daily active users (counted each night for the day before)"],
            ["Jobs", "Generation runs, Generation time p95, Job time p95, Failed jobs, Portal syncs, Portal records synced"],
            ["Usage", "Sign ins, Uploads, Uploaded bytes, Exports, Exported bytes"],
            ["Storage", "Database schema size, Uploaded files size (logos on Cloudinary aren't counted)"],
          ]}
        />
        <p>Links on the tab open Traces and Logs already filtered to the school.</p>
      </Section>

      <Section id="traces" eyebrow="Page" title="Traces">
        <p>
          A trace is the full journey of one request. If that request started a background job, the job's steps are in
          the same trace. Each part that ran in one process (the API request, the job, a WebSocket message) is called a{" "}
          <em>segment</em>. Open them in <Link to="/console/observability/traces" className={pageLink}>Traces</Link>.
        </p>
        <Subheading>Which traces are kept</Subheading>
        <ul>
          <li><strong>Always: errors.</strong> A 5xx response, an unhandled exception, a job that failed for good, or an exception in a WebSocket handler.</li>
          <li><strong>Always: slow work.</strong> Requests over 1 second and jobs over 30 seconds.</li>
          <li><strong>Sampled: everything else.</strong> 10% is kept by default.</li>
        </ul>
        <p>4xx responses, job retries and expected failures don't count as errors. Traces are kept for 3 days.</p>
        <Subheading>The trace list</Subheading>
        <ul>
          <li><strong>Filters</strong>: school, range, kind (Request, Job, WebSocket), status, errors only, minimum duration, and route.</li>
          <li><strong>Columns</strong>: time, school, kind, name or route, status, duration, spans, and flags (error, slow, truncated). Newest first.</li>
          <li><strong>Find a trace by reference</strong>: paste a user's reference code, or any trace id prefix of 8 or more characters. This searches all retained days.</li>
        </ul>
        <Subheading>Trace detail</Subheading>
        <ul>
          <li><strong>Header</strong>: the trace id (copyable) and, for each segment, its school, kind, name, status, duration, release, user id and request id.</li>
          <li><strong>Waterfall</strong>: one row per step, indented under the step that started it, with bars placed by start time, sized by duration and coloured by kind. Failed steps are marked. Each segment starts its own labelled group.</li>
          <li><strong>Step details</strong>: click a row to see what it recorded. SQL is stored with <Code>%s</Code> placeholders; bound values such as names are never stored.</li>
          <li><strong>Logs</strong>: the log lines written during the trace, under the waterfall.</li>
        </ul>
        <p>A "Not sampled" row means a parent step wasn't stored, usually when a kept job ran under a request that wasn't kept.</p>
      </Section>

      <Section id="logs" eyebrow="Page" title="Logs">
        <p>
          <Link to="/console/observability/logs" className={pageLink}>Logs</Link> are kept for 7 days, and a line is
          stored even when its trace isn't. Stored: INFO and above from Ordo's own code (<Code>ems</Code>,{" "}
          <Code>tenancy</Code>, <Code>core</Code>), and ERROR and above from Django request handling and Celery.
        </p>
        <ul>
          <li><strong>Filters</strong>: school, range, level, logger prefix (such as <Code>ems.tasks</Code>), and message search (part of a word or an id). Trace, request id and user id filters are set when you follow a link.</li>
          <li><strong>Columns</strong>: time, level, school, logger, message, and a link to the trace when one was stored.</li>
        </ul>
        <Note title="Live.">
          Checks for new lines every 5 seconds and adds them at the top. Up to 1000 lines stay on screen, and checking
          pauses while the browser tab is hidden.
        </Note>
      </Section>

      <Section id="metrics" eyebrow="Page" title="Metrics">
        <p>The <Link to="/console/observability/metrics" className={pageLink}>metric explorer</Link> can chart anything Ordo records.</p>
        <ol>
          <li>Pick a <strong>Metric</strong>. Each one shows its description and unit.</li>
          <li>Pick an <strong>Aggregation</strong>. Only the ones that make sense for that metric are offered.</li>
          <li>Optionally pick a <strong>Group</strong> to draw one line per label value, and filter on a label's value.</li>
          <li>Pick a <strong>School</strong> (or all schools) and a <strong>Range</strong> (up to 90 days).</li>
        </ol>
        <p>
          Ranges up to 48 hours use minute data, kept 7 days. Longer ranges use hourly data, kept 90 days. Percentiles
          are accurate to within one histogram bucket.
        </p>
        <Note title="Gaps and zeros.">
          A zero means nothing happened in that minute. A gap means there's no data: the minute isn't stored yet, or
          telemetry was dropped.
        </Note>
        <Subheading>Built in metrics</Subheading>
        <GuideTable head={["Metric", "Type", "What it measures"]} rows={BUILT_IN_METRICS} code={[0]} />
        <Subheading>Business metrics</Subheading>
        <GuideTable head={["Metric", "Type", "What it measures"]} rows={BUSINESS_METRICS} code={[0]} />
        <p>
          "Platform" metrics have no per school values, so picking a school doesn't change them. Every metric is declared
          in <Code>tenancy/observability/metrics.py</Code>; anything not declared there is refused.
        </p>
      </Section>

      <Section id="dashboards" eyebrow="Page" title="Dashboards">
        <p><Link to="/console/observability/dashboards" className={pageLink}>Dashboards</Link> are shared sets of charts saved from the metric explorer.</p>
        <ul>
          <li><strong>Save a chart</strong>: in Metrics, click <strong>Save to dashboard</strong>, title the chart, and pick a dashboard or name a new one.</li>
          <li><strong>School</strong>: a chart saved with a school picked stays fixed to it. Otherwise it follows the dashboard's school picker, so one dashboard can serve any school.</li>
          <li><strong>Time range</strong>: every chart uses the dashboard's range.</li>
          <li><strong>Edit mode</strong>: rename, reorder or remove charts. Only Owners can delete a dashboard.</li>
        </ul>
      </Section>

      <Section id="alerts" eyebrow="Page" title="Alerts">
        <p>
          <Link to="/console/observability/alerts" className={pageLink}>Alert rules</Link> send an email when a metric
          crosses a threshold and stays there. Only Owners can change them.
        </p>
        <GuideTable
          head={["Field", "Meaning"]}
          rows={[
            ["Metric", "Any declared metric"],
            ["Aggregation", "Counters: rate, sum. Histograms: rate, sum, avg, p95, max. Gauges: last, max, avg"],
            ["Only count", "Optional label filters, for example status_class = 5xx"],
            ["Fires when the value is", "Above or Below the threshold"],
            ["For every minute of the last", "1 to 120 minutes"],
            ["Scope", "Each school separately, or Platform total. Platform only metrics allow Platform total only"],
            ["On", "Turns the rule on or off without deleting it"],
          ]}
        />
        <Subheading>How rules are checked</Subheading>
        <ul>
          <li>Every minute, against complete minutes up to two minutes ago.</li>
          <li>An incident opens when the condition holds for <strong>every</strong> minute of the window, with one email to <Code>PLATFORM_ALERT_EMAILS</Code> (or the active Owners).</li>
          <li>It resolves after <strong>5 clear checks in a row</strong>, with one more email. No repeats while it's open.</li>
          <li><strong>Missing data never fires or resolves a rule</strong>; the target shows "no data".</li>
          <li>Changing what a rule measures or its threshold, window or scope quietly resolves its open incidents.</li>
          <li>A built in alert emails the Owners if telemetry hasn't reached Postgres for 5 minutes.</li>
        </ul>
        <Subheading>Example rules</Subheading>
        <p>Starting points; tune the thresholds to your traffic.</p>
        <GuideTable head={["Goal", "Metric", "Aggregation", "Filter", "Condition", "Scope"]} rows={EXAMPLE_RULES} code={[1, 3]} />
      </Section>

      <Section id="tasks" eyebrow="How to" title="Common tasks">
        <Subheading>
          <span id="find-error" className="scroll-mt-6">Finding a user's error</span>
        </Subheading>
        <ol>
          <li>The school user sees <strong>Reference: <Code>ab12cd34</Code></strong> on the error toast or error page. Ask them for it.</li>
          <li>Open <Link to="/console/observability/traces" className={pageLink}>Traces</Link> and paste it into <strong>Find a trace by reference</strong>.</li>
          <li>Open the trace. The failed step is marked in the waterfall, and the logs are listed under it.</li>
        </ol>
        <Subheading>Why is this school slow?</Subheading>
        <ol>
          <li>Open the school's Observability tab. Check Latency p95 and Job time p95 over the last 24 hours.</li>
          <li>On the Overview, pick the school and click the worst route in <strong>Slowest routes</strong>.</li>
          <li>In Traces, set a minimum duration and open a result. Look for many repeated database steps or one long step.</li>
        </ol>
        <Subheading>What happened at 10:42?</Subheading>
        <p>
          Open <Link to="/console/observability/logs" className={pageLink}>Logs</Link>, filter by school, set the range to
          cover the time, and search for an id or keyword. Click a trace link to see the whole request.
        </p>
      </Section>

      <Section id="troubleshooting" eyebrow="When something looks wrong" title="Troubleshooting">
        <GuideTable
          head={["Symptom", "Likely cause", "What to do"]}
          rows={[
            ["Charts are empty or end a few minutes ago", "The flush isn't running", "Check worker-observability (production) or a Celery worker on the observability queue (local). A stall over 5 minutes sends an alert email"],
            ["Shaded minutes on charts", "The telemetry Redis was down or full", "Check redis-obs memory. Drops are counted in obs.dropped by reason"],
            ["A trace you expect is missing", "It was fast and not sampled, or it's older than 3 days", "Look in Logs instead (lines are always kept), or raise OBS_TRACE_SAMPLE_PERCENT"],
            ["No traces, logs or metrics at all", "A signal is switched off", "Check the OBSERVABILITY_*_ENABLED switches and OBSERVABILITY_ENABLED"],
            ["Uploaded files chart is empty", "No media volume is mounted", "Expected in production today; logos are on Cloudinary"],
          ]}
        />
      </Section>

      <Section id="settings" eyebrow="For whoever runs the server" title="Settings">
        <GuideTable head={["Setting", "Default", "Effect"]} rows={SETTINGS} code={[0]} />
        <p>
          Old data is removed every night at 00:15 UTC by dropping whole daily partitions. Purging a school deletes all
          of its telemetry straight away.
        </p>
      </Section>
    </div>
  );
}
