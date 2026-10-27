import type { ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { FileText, Waypoints } from "lucide-react";
import {
  METRIC_RANGE_LABELS,
  type MetricLine,
  type MetricPanel,
  type MetricRange,
  useObservabilityOverview,
  useSchoolObservability,
  useSchoolOptions,
  useSlowRoutes,
  useTopSchools,
} from "@/api/telemetry";
import { PageHeader } from "@/components/layout/page-header";
import { type ChartSeries, TimeSeriesChart } from "@/components/time-series-chart";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { extractErrorEnvelope } from "@/lib/api";
import { FilterSelect, entries, formatMs } from "./traces";

export interface ObservabilitySearch {
  range?: MetricRange;
  school?: number;
}

type Format = (value: number) => string;

const count: Format = (v) => v.toLocaleString(undefined, { maximumFractionDigits: 1 });
const perSecond: Format = (v) => `${v.toLocaleString(undefined, { maximumFractionDigits: v < 10 ? 2 : 0 })}/s`;
const share: Format = (v) => `${(v * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
const percent: Format = (v) => `${Math.round(v)}%`;
const ms: Format = (v) => formatMs(v);

export const bytes: Format = (v) => {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (Math.abs(v) >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toLocaleString(undefined, { maximumFractionDigits: v < 10 && i ? 1 : 0 })} ${units[i]}`;
};

/** How to show a metric's values, from its unit and how it is aggregated. */
export function formatFor(unit: string, aggregation: string): Format {
  if (unit === "ms" && aggregation !== "rate" && aggregation !== "sum") return ms;
  if (unit === "By") return aggregation === "rate" ? (v) => `${bytes(v)}/s` : bytes;
  if (unit === "%") return percent;
  return aggregation === "rate" ? perSecond : count;
}

/** A line's name: its label value, or the panel's title when ungrouped. */
function lineName(line: MetricLine, fallback: string): string {
  const values = Object.values(line.labels);
  if (!values.length) return fallback;
  const value = values[0];
  return value === null || value === "" ? "none" : String(value);
}

function toSeries(panel: MetricPanel | undefined, title: string): ChartSeries[] {
  return (panel?.series ?? []).map((line) => ({ label: lineName(line, title), points: line.points }));
}

function MetricCard({
  title,
  description,
  panel,
  format,
  loading,
}: {
  title: string;
  description?: string;
  panel: MetricPanel | undefined;
  format: Format;
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-[14px]">{title}</CardTitle>
        {description && <p className="text-[12px] text-muted-foreground">{description}</p>}
      </CardHeader>
      <CardContent>
        {loading && !panel ? (
          <Skeleton className="h-[180px] w-full" />
        ) : (
          <TimeSeriesChart
            series={toSeries(panel, title)}
            label={title}
            format={format}
            dropped={panel?.dropped}
            step={panel?.step}
          />
        )}
      </CardContent>
    </Card>
  );
}

function Grid({ children }: { children: ReactNode }) {
  return <div className="grid gap-4 lg:grid-cols-2">{children}</div>;
}

function Heading({ children }: { children: ReactNode }) {
  return <h2 className="font-serif text-[1.35rem] leading-tight tracking-[-0.01em]">{children}</h2>;
}

function PanelsError({ error }: { error: unknown }) {
  return (
    <Alert variant="destructive">
      <AlertDescription>{extractErrorEnvelope(error).detail}</AlertDescription>
    </Alert>
  );
}

/**
 * The platform at a glance (spec 0006, AC-21): traffic, errors, latency,
 * jobs, the host and the database, for all schools or one. A gap in a chart
 * is a minute Ordo doesn't know about yet (or dropped); a flat line at zero
 * really was zero. A shaded stretch is where telemetry was dropped (AC-4).
 */
export default function ConsoleObservabilityPage({ search }: { search: ObservabilitySearch }) {
  const navigate = useNavigate();
  const range = search.range ?? "24h";
  const overview = useObservabilityOverview(range, search.school);
  const routes = useSlowRoutes(range, search.school);
  const top = useTopSchools(range);
  const schools = useSchoolOptions();
  const panels = overview.data?.panels;
  const loading = overview.isLoading;

  const setSearch = (patch: ObservabilitySearch) =>
    void navigate({ to: "/console/observability", search: { ...search, ...patch }, replace: true });

  const schoolOptions: Array<[string, string]> = (schools.data ?? [])
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((s) => [String(s.id), s.name]);

  return (
    <div className="space-y-8">
      <PageHeader
        section="Console · Observability"
        title="Overview"
        description="How Ordo is running: traffic, errors and latency across schools, the job queues, and the server."
      />

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
        <FilterSelect
          label="Range"
          value={range}
          options={entries(METRIC_RANGE_LABELS)}
          onChange={(value) => setSearch({ range: value === "24h" ? undefined : value })}
          anyLabel=""
        />
        <FilterSelect
          label="School"
          value={search.school === undefined ? undefined : String(search.school)}
          options={schoolOptions}
          onChange={(value) => setSearch({ school: value ? Number(value) : undefined })}
          anyLabel="All schools"
          width="w-[200px]"
        />
        <Link
          to="/console/observability/traces"
          search={{ school: search.school === undefined ? undefined : String(search.school) }}
          className={buttonVariants({ variant: "ghost", size: "sm" })}
        >
          <Waypoints data-icon="inline-start" />
          Traces
        </Link>
      </div>

      {overview.error ? <PanelsError error={overview.error} /> : null}

      <section className="space-y-4" aria-label="Traffic">
        <Heading>Traffic</Heading>
        <Grid>
          <MetricCard title="Requests" panel={panels?.requests} format={perSecond} loading={loading} />
          <MetricCard
            title="Error rate"
            description="Share of requests that ended in a server error (5xx)."
            panel={panels?.error_rate}
            format={share}
            loading={loading}
          />
          <MetricCard title="Latency, p95" panel={panels?.latency_p95} format={ms} loading={loading} />
          <MetricCard
            title="Job wait, p95"
            description="Time a job waited in its queue before starting."
            panel={panels?.queue_wait_p95}
            format={ms}
            loading={loading}
          />
        </Grid>
      </section>

      <section className="space-y-4" aria-label="Server">
        <Heading>Server</Heading>
        <p className="text-[13px] text-muted-foreground">
          The whole platform, whichever school is picked.
        </p>
        <Grid>
          <MetricCard title="Jobs waiting" panel={panels?.queue_depth} format={count} loading={loading} />
          <MetricCard title="CPU" panel={panels?.cpu} format={percent} loading={loading} />
          <MetricCard title="Memory" panel={panels?.memory} format={percent} loading={loading} />
          <MetricCard title="Disk free" panel={panels?.disk_free} format={bytes} loading={loading} />
          <MetricCard title="Disk used" panel={panels?.disk_used} format={bytes} loading={loading} />
          <MetricCard title="Database size" panel={panels?.db_size} format={bytes} loading={loading} />
        </Grid>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <SlowRoutesCard routes={routes} school={search.school} />
        <TopSchoolsCard top={top} range={range} />
      </div>
    </div>
  );
}

function TableState({ loading, error, empty }: { loading: boolean; error: unknown; empty: boolean }) {
  if (error) return <PanelsError error={error} />;
  if (loading)
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  if (empty) return <p className="py-6 text-center text-[13px] text-muted-foreground">No requests in this range. Try a wider range.</p>;
  return null;
}

function SlowRoutesCard({ routes, school }: { routes: ReturnType<typeof useSlowRoutes>; school?: number }) {
  const rows = routes.data?.results ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-[14px]">Slowest routes</CardTitle>
      </CardHeader>
      <CardContent>
        <TableState loading={routes.isLoading} error={routes.error} empty={!rows.length} />
        {rows.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Route</TableHead>
                <TableHead className="text-right">p95</TableHead>
                <TableHead className="text-right">Requests</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.route}>
                  <TableCell className="max-w-[260px]">
                    <Link
                      to="/console/observability/traces"
                      search={{ route: row.route, school: school === undefined ? undefined : String(school) }}
                      className="block truncate font-mono text-[12px] hover:underline"
                      title="Traces for this route"
                    >
                      {row.route}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{formatMs(row.p95_ms)}</TableCell>
                  <TableCell className="text-right tabular-nums">{row.requests.toLocaleString()}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function TopSchoolsCard({ top, range }: { top: ReturnType<typeof useTopSchools>; range: MetricRange }) {
  const rows = top.data?.results ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-[14px]">Busiest schools</CardTitle>
      </CardHeader>
      <CardContent>
        <TableState loading={top.isLoading} error={top.error} empty={!rows.length} />
        {rows.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>School</TableHead>
                <TableHead className="text-right">Requests</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.school.id}>
                  <TableCell>
                    <Link
                      to="/console/schools/$id"
                      params={{ id: String(row.school.id) }}
                      search={{ tab: "observability", range }}
                      className="hover:underline"
                    >
                      {row.school.name}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{row.requests.toLocaleString()}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

/** A school's own numbers, for the Observability tab of its page (AC-21). */
export function SchoolObservability({
  schoolId,
  range,
  onRange,
}: {
  schoolId: number;
  range: MetricRange;
  onRange: (range: MetricRange) => void;
}) {
  const data = useSchoolObservability(schoolId, range);
  const panels = data.data?.panels;
  const loading = data.isLoading;
  const card = (title: string, key: keyof NonNullable<typeof panels>, format: Format, description?: string) => (
    <MetricCard title={title} description={description} panel={panels?.[key]} format={format} loading={loading} />
  );

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
        <FilterSelect
          label="Range"
          value={range}
          options={entries(METRIC_RANGE_LABELS)}
          onChange={(value) => onRange(value ?? "24h")}
          anyLabel=""
        />
        <Link
          to="/console/observability/traces"
          search={{ school: String(schoolId) }}
          className={buttonVariants({ variant: "ghost", size: "sm" })}
        >
          <Waypoints data-icon="inline-start" />
          Traces for this school
        </Link>
        <Link
          to="/console/observability/logs"
          search={{ school: String(schoolId) }}
          className={buttonVariants({ variant: "ghost", size: "sm" })}
        >
          <FileText data-icon="inline-start" />
          Logs for this school
        </Link>
      </div>

      {data.error ? <PanelsError error={data.error} /> : null}

      <section className="space-y-4" aria-label="Traffic">
        <Heading>Traffic</Heading>
        <Grid>
          {card("Requests", "requests", perSecond)}
          {card("Error rate", "error_rate", share, "Share of requests that ended in a server error (5xx).")}
          {card("Latency, p95", "latency_p95", ms)}
          {card("Daily active users", "daily_active", count, "Counted each night for the day before.")}
        </Grid>
      </section>

      <section className="space-y-4" aria-label="Jobs">
        <Heading>Jobs</Heading>
        <Grid>
          {card("Generation runs", "runs", count, "Timetable, distribution and allocation runs.")}
          {card("Generation time, p95", "run_duration_p95", ms)}
          {card("Job time, p95", "job_duration_p95", ms)}
          {card("Failed jobs", "job_failures", count)}
          {card("Portal syncs", "syncs", count)}
          {card("Portal records synced", "sync_records", count)}
        </Grid>
      </section>

      <section className="space-y-4" aria-label="Usage">
        <Heading>Usage</Heading>
        <Grid>
          {card("Sign ins", "logins", count)}
          {card("Uploads", "uploads", count)}
          {card("Uploaded", "upload_bytes", bytes)}
          {card("Exports", "exports", count)}
          {card("Exported", "export_bytes", bytes)}
        </Grid>
      </section>

      <section className="space-y-4" aria-label="Storage">
        <Heading>Storage</Heading>
        <Grid>
          {card("Database schema", "schema_bytes", bytes)}
          {card("Uploaded files", "media_bytes", bytes, "Files stored on the server; logos on Cloudinary aren't counted.")}
        </Grid>
      </section>
    </div>
  );
}
