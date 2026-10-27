import { useEffect, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { platformApi } from "@/lib/api";
import type { PaginatedResponse } from "./types";
import type { SchoolRow } from "./platform";

/** Metrics, logs and traces in the console (spec 0006). */

export type SegmentKind = "http" | "job" | "ws";
export type SpanKind = "server" | "db" | "redis" | "job" | "ws" | "http_out" | "internal";
export type StatusClass = "2xx" | "4xx" | "5xx";

export const SEGMENT_KIND_LABELS: Record<SegmentKind, string> = {
  http: "Request",
  job: "Job",
  ws: "WebSocket",
};

export const SPAN_KIND_LABELS: Record<SpanKind, string> = {
  server: "Request",
  db: "Database",
  redis: "Redis",
  job: "Job",
  ws: "WebSocket",
  http_out: "Outgoing call",
  internal: "Internal",
};

export const STATUS_CLASS_LABELS: Record<StatusClass, string> = {
  "2xx": "2xx success",
  "4xx": "4xx refused",
  "5xx": "5xx error",
};

export interface TelemetrySchoolRef {
  id: number;
  name: string;
  slug: string;
}

/** The spans of one request, job or WebSocket message in one process. */
export interface Segment {
  root_span_id: string;
  trace_id: string;
  started_at: string;
  school_id: number | null;
  /** Null is Platform: the console, or work outside any school. */
  school: TelemetrySchoolRef | null;
  kind: SegmentKind;
  root_name: string;
  route: string;
  method: string;
  status_code: number | null;
  duration_ms: number;
  is_error: boolean;
  is_slow: boolean;
  span_count: number;
  truncated: boolean;
  user_id: number | null;
  request_id: string;
  release: string;
  process_role: string;
}

export interface Span {
  span_id: string;
  parent_span_id: string | null;
  root_span_id: string;
  started_at: string;
  school_id: number | null;
  name: string;
  kind: SpanKind;
  duration_ms: number;
  status: "ok" | "error";
  /** Only allowlisted keys are ever stored. */
  attributes: Record<string, string | number | boolean | Array<string | number>>;
}

/** One stored log line. Logs aren't sampled: a line keeps its trace id even
 * when that trace wasn't kept. */
export interface LogLine {
  id: number;
  logged_at: string;
  school_id: number | null;
  school: TelemetrySchoolRef | null;
  /** The Python level number: 20 info, 30 warning, 40 error, 50 critical. */
  level: number;
  logger: string;
  message: string;
  trace_id: string | null;
  span_id: string | null;
  request_id: string;
  user_id: number | null;
  attributes: Record<string, string | number | boolean | null>;
}

export type TraceLog = LogLine;

export interface SearchedLogLine extends LogLine {
  /** Whether the line's trace was kept, so it can be opened. */
  trace_stored: boolean;
}

export type LogLevel = "info" | "warning" | "error" | "critical";

export const LOG_LEVEL_LABELS: Record<LogLevel, string> = {
  info: "Info and above",
  warning: "Warnings and above",
  error: "Errors and above",
  critical: "Critical only",
};

/** "Info", "Warning" and so on, from a level number. */
export function levelName(level: number): string {
  if (level >= 50) return "Critical";
  if (level >= 40) return "Error";
  if (level >= 30) return "Warning";
  if (level >= 20) return "Info";
  return "Debug";
}

export interface TraceDetail {
  trace_id: string;
  segments: Segment[];
  spans: Span[];
  logs: TraceLog[];
  /** Parents some span names that were not stored (not sampled, or outside Ordo). */
  missing_parents: string[];
}

export interface CursorPage<T> {
  results: T[];
  next: string | null;
  previous: string | null;
}

export interface LogPage extends CursorPage<SearchedLogLine> {
  /** Pass back as ``after`` to get only lines flushed since this page. */
  latest: string;
}

/** A school id, "platform", or undefined for all. */
export type TelemetryScope = string | undefined;

export type TraceRange = "1h" | "6h" | "24h" | "all";

export const TRACE_RANGE_LABELS: Record<TraceRange, string> = {
  "1h": "Last hour",
  "6h": "Last 6 hours",
  "24h": "Last 24 hours",
  all: "Everything kept",
};

const RANGE_MS: Record<Exclude<TraceRange, "all">, number> = {
  "1h": 3_600_000,
  "6h": 6 * 3_600_000,
  "24h": 24 * 3_600_000,
};

export interface TraceFilters {
  school?: TelemetryScope;
  kind?: SegmentKind;
  status?: StatusClass;
  errors?: boolean;
  min_ms?: number;
  route?: string;
  /** Any prefix of 8 or more characters of a trace id. */
  trace?: string;
  range?: TraceRange;
}

const TELEMETRY = ["platform", "telemetry"] as const;

/** ``start`` for a range; ``all`` (and a trace search) lets the server use every day kept. */
function rangeStart(range: TraceRange | undefined, trace: string | undefined): string | undefined {
  if (trace || range === "all") return undefined;
  return new Date(Date.now() - RANGE_MS[range ?? "1h"]).toISOString();
}

export interface LogFilters {
  school?: TelemetryScope;
  level?: LogLevel;
  /** Matches the logger name and everything under it, such as "ems.tasks". */
  logger?: string;
  trace?: string;
  request?: string;
  user?: number;
  /** Part of the message, 3 characters or more. */
  q?: string;
  range?: TraceRange;
}

const LIVE_EVERY_MS = 5_000;
const LIVE_KEEP = 500;

function logParams(filters: LogFilters) {
  const { range, ...rest } = filters;
  // A trace or a request names its lines: look across every day kept.
  return { ...rest, start: rangeStart(range, filters.trace ?? filters.request) };
}

async function fetchLogs(filters: LogFilters, extra: { cursor?: string; after?: string }) {
  return (
    await platformApi.get<LogPage>("/observability/logs/", { params: { ...logParams(filters), ...extra } })
  ).data;
}

export function useLogs(filters: LogFilters, cursor: string | null) {
  return useQuery({
    queryKey: [...TELEMETRY, "logs", filters, cursor],
    queryFn: () => fetchLogs(filters, { cursor: cursor ?? undefined }),
    placeholderData: keepPreviousData,
  });
}

/** Newest first; a line seen twice keeps one copy. */
function mergeLines(fresh: SearchedLogLine[], seen: SearchedLogLine[]): SearchedLogLine[] {
  const ids = new Set(fresh.map((l) => l.id));
  return [...fresh, ...seen.filter((l) => !ids.has(l.id))].sort(
    (a, b) => b.logged_at.localeCompare(a.logged_at) || b.id - a.id,
  );
}

/**
 * Lines flushed after ``latest``, polled every 5 seconds while ``enabled``
 * and the tab is visible. Starts over when the filters or ``latest`` change.
 */
export function useLiveLogs(filters: LogFilters, latest: string | undefined, enabled: boolean) {
  const [lines, setLines] = useState<SearchedLogLine[]>([]);
  const after = useRef(latest);
  const key = JSON.stringify(filters);

  useEffect(() => {
    after.current = latest;
    setLines([]);
  }, [latest, key]);

  useEffect(() => {
    if (!enabled) return;
    const current = JSON.parse(key) as LogFilters;
    let busy = false;
    const timer = window.setInterval(async () => {
      if (busy || !after.current || document.hidden) return;
      busy = true;
      try {
        const page = await fetchLogs(current, { after: after.current });
        after.current = page.latest;
        if (page.results.length) setLines((seen) => mergeLines(page.results, seen).slice(0, LIVE_KEEP));
      } catch {
        // A missed poll is retried on the next tick.
      } finally {
        busy = false;
      }
    }, LIVE_EVERY_MS);
    return () => window.clearInterval(timer);
  }, [enabled, key]);

  return lines;
}

/** Live lines on top of the page, without repeats. */
export function withLiveLines(live: SearchedLogLine[], page: SearchedLogLine[]): SearchedLogLine[] {
  return live.length ? mergeLines(live, page) : page;
}

export function useTraces(filters: TraceFilters, cursor: string | null) {
  return useQuery({
    queryKey: [...TELEMETRY, "traces", filters, cursor],
    queryFn: async () => {
      const { range, errors, ...rest } = filters;
      const params = {
        ...rest,
        errors: errors ? "true" : undefined,
        start: rangeStart(range, filters.trace),
        cursor: cursor ?? undefined,
      };
      return (await platformApi.get<CursorPage<Segment>>("/observability/traces/", { params })).data;
    },
    placeholderData: keepPreviousData,
  });
}

export function useTrace(traceId: string) {
  return useQuery({
    queryKey: [...TELEMETRY, "trace", traceId],
    queryFn: async () =>
      (await platformApi.get<TraceDetail>(`/observability/traces/${traceId}/`)).data,
    retry: false,
  });
}

/** Schools for a filter picker, by name: the first 100. */
export function useSchoolOptions() {
  return useQuery({
    queryKey: ["platform", "schools", "options"],
    queryFn: async () =>
      (
        await platformApi.get<PaginatedResponse<SchoolRow>>("/schools/", {
          params: { page_size: 100 },
        })
      ).data.results,
    staleTime: 5 * 60_000,
  });
}

/** Metric panels (spec 0006, AC-21). */

export type MetricRange = "1h" | "6h" | "24h" | "7d" | "30d" | "90d";

export const METRIC_RANGE_LABELS: Record<MetricRange, string> = {
  "1h": "Last hour",
  "6h": "Last 6 hours",
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "90d": "Last 90 days",
};

const METRIC_RANGE_MS: Record<MetricRange, number> = {
  "1h": 3_600_000,
  "6h": 6 * 3_600_000,
  "24h": 24 * 3_600_000,
  "7d": 7 * 24 * 3_600_000,
  "30d": 30 * 24 * 3_600_000,
  "90d": 90 * 24 * 3_600_000,
};

/** One line: label values (empty for an ungrouped panel) and ``[epoch s, value | null]`` points. */
export interface MetricLine {
  labels: Record<string, string | number | null>;
  points: Array<[number, number | null]>;
}

export interface MetricPanel {
  metric: string;
  /** "1", "ms", "By" or "%". */
  unit: string;
  type: "counter" | "histogram" | "gauge";
  step: number;
  series: MetricLine[];
  /** Buckets in which some telemetry was dropped (shaded on the chart). */
  dropped: number[];
}

export interface PanelsResponse<K extends string> {
  start: string;
  end: string;
  school: number | null;
  panels: Record<K, MetricPanel>;
}

export type OverviewPanel =
  | "requests"
  | "error_rate"
  | "latency_p95"
  | "queue_depth"
  | "queue_wait_p95"
  | "cpu"
  | "memory"
  | "disk_used"
  | "disk_free"
  | "db_size";

export type SchoolPanel =
  | "requests"
  | "error_rate"
  | "latency_p95"
  | "job_duration_p95"
  | "job_failures"
  | "runs"
  | "run_duration_p95"
  | "syncs"
  | "sync_records"
  | "logins"
  | "daily_active"
  | "uploads"
  | "upload_bytes"
  | "exports"
  | "export_bytes"
  | "schema_bytes"
  | "media_bytes";

export interface SlowRoute {
  route: string;
  p95_ms: number;
  requests: number;
}

export interface TopSchool {
  school: TelemetrySchoolRef;
  requests: number;
}

const PANELS_REFRESH_MS = 60_000;

function metricWindow(range: MetricRange) {
  const end = new Date();
  return { start: new Date(end.getTime() - METRIC_RANGE_MS[range]).toISOString(), end: end.toISOString() };
}

function usePanels<T>(key: unknown[], path: string, range: MetricRange, params: Record<string, unknown> = {}) {
  return useQuery({
    queryKey: [...TELEMETRY, ...key, range, params],
    queryFn: async () => (await platformApi.get<T>(path, { params: { ...params, ...metricWindow(range) } })).data,
    placeholderData: keepPreviousData,
    refetchInterval: PANELS_REFRESH_MS,
  });
}

/** The platform panels, for every school or the one picked. */
export function useObservabilityOverview(range: MetricRange, school?: number) {
  return usePanels<PanelsResponse<OverviewPanel>>(["overview"], "/observability/overview/", range, { school });
}

export function useSchoolObservability(schoolId: number, range: MetricRange) {
  return usePanels<PanelsResponse<SchoolPanel>>(
    ["school-overview", schoolId],
    `/observability/schools/${schoolId}/overview/`,
    range,
  );
}

export function useSlowRoutes(range: MetricRange, school?: number) {
  return usePanels<{ results: SlowRoute[] }>(["routes"], "/observability/routes/", range, { school });
}

export function useTopSchools(range: MetricRange) {
  return usePanels<{ results: TopSchool[] }>(["top-schools"], "/observability/top-schools/", range);
}

/** The metric explorer and shared dashboards (spec 0006, AC-24). */

export type Aggregation = "rate" | "sum" | "avg" | "p50" | "p95" | "p99" | "max" | "last";

export const AGGREGATION_LABELS: Record<Aggregation, string> = {
  rate: "Rate per second",
  sum: "Sum",
  avg: "Average",
  p50: "p50",
  p95: "p95",
  p99: "p99",
  max: "Max",
  last: "Last value",
};

export interface RegistryMetric {
  name: string;
  type: MetricPanel["type"];
  unit: string;
  /** "platform" metrics have no per school values. */
  scope: "school" | "platform";
  description: string;
  /** Label name to its allowed values, or null for any value. */
  labels: Record<string, string[] | null>;
  aggregations: Aggregation[];
  group_by: string[];
}

export function useMetricRegistry() {
  return useQuery({
    queryKey: [...TELEMETRY, "registry"],
    queryFn: async () =>
      (await platformApi.get<{ results: RegistryMetric[] }>("/observability/metrics/registry/")).data.results,
    staleTime: Infinity,
  });
}

export interface MetricQuery {
  metric: string;
  aggregation: Aggregation;
  group_by?: string;
  filters?: Record<string, string>;
  /** One school; unset reads all schools together. Ignored by platform metrics. */
  school?: number;
}

export interface MetricQueryResult extends MetricPanel {
  /** Buckets with no reliable data yet (drawn as gaps). */
  missing: number[];
  /** Names for a query grouped by school, by id. */
  schools?: Record<string, TelemetrySchoolRef>;
}

/** One explorer chart over ``range``, refreshed every minute. */
export function useMetricQuery(query: MetricQuery | null, range: MetricRange) {
  return useQuery({
    queryKey: [...TELEMETRY, "explore", query, range],
    queryFn: async () =>
      (
        await platformApi.post<MetricQueryResult>("/observability/metrics/query/", {
          ...query,
          ...metricWindow(range),
          mode: "series",
        })
      ).data,
    enabled: query !== null,
    placeholderData: keepPreviousData,
    refetchInterval: PANELS_REFRESH_MS,
  });
}

export interface AdminRef {
  id: number;
  name: string;
}

export interface ChartInput {
  title: string;
  metric_name: string;
  aggregation: Aggregation;
  group_by: string;
  filters: Record<string, string>;
  /** A fixed school; null follows the dashboard's school picker. */
  school: number | null;
}

export interface SavedChart extends Omit<ChartInput, "school"> {
  id: number;
  school: (TelemetrySchoolRef & { purged: boolean }) | null;
}

export interface DashboardRow {
  id: number;
  name: string;
  chart_count: number;
  created_by: AdminRef | null;
  updated_by: AdminRef | null;
  created_at: string;
  updated_at: string;
}

export interface DashboardDetail extends Omit<DashboardRow, "chart_count"> {
  charts: SavedChart[];
}

const DASHBOARDS = [...TELEMETRY, "dashboards"] as const;

/** A saved chart as the API takes it back. */
export function chartInput(chart: SavedChart): ChartInput {
  return { ...chart, school: chart.school?.id ?? null };
}

export function useDashboards() {
  return useQuery({
    queryKey: DASHBOARDS,
    queryFn: async () =>
      (await platformApi.get<{ results: DashboardRow[] }>("/observability/dashboards/")).data.results,
  });
}

export function useDashboard(id: number) {
  return useQuery({
    queryKey: [...DASHBOARDS, id],
    queryFn: async () => (await platformApi.get<DashboardDetail>(`/observability/dashboards/${id}/`)).data,
    retry: false,
  });
}

function useDashboardMutation<I, O>(fn: (input: I) => Promise<O>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => void qc.invalidateQueries({ queryKey: DASHBOARDS }),
  });
}

export const useCreateDashboard = () =>
  useDashboardMutation(
    async (input: { name: string; charts: ChartInput[] }) =>
      (await platformApi.post<DashboardDetail>("/observability/dashboards/", input)).data,
  );

export const useUpdateDashboard = () =>
  useDashboardMutation(
    async ({ id, ...input }: { id: number; name?: string; charts?: ChartInput[] }) =>
      (await platformApi.patch<DashboardDetail>(`/observability/dashboards/${id}/`, input)).data,
  );

/** Adds ``chart`` at the end of a dashboard, keeping what others saved there. */
export const useAddChart = () =>
  useDashboardMutation(async ({ id, chart }: { id: number; chart: ChartInput }) => {
    const current = (await platformApi.get<DashboardDetail>(`/observability/dashboards/${id}/`)).data;
    const charts = [...current.charts.map(chartInput), chart];
    return (await platformApi.patch<DashboardDetail>(`/observability/dashboards/${id}/`, { charts })).data;
  });

export const useDeleteDashboard = () =>
  useDashboardMutation((id: number) => platformApi.delete(`/observability/dashboards/${id}/`));

/** Alert rules and incidents (spec 0006, AC-17 to AC-19). */

export type AlertState = "ok" | "firing" | "no_data";
export type AlertScope = "per_school" | "platform";
export type Comparison = "gt" | "lt";

/** What a rule may aggregate, by metric type (child 0004). */
export const RULE_AGGREGATIONS: Record<MetricPanel["type"], Aggregation[]> = {
  counter: ["rate", "sum"],
  histogram: ["rate", "sum", "avg", "p95", "max"],
  gauge: ["last", "max", "avg"],
};

export const ALERT_SCOPE_LABELS: Record<AlertScope, string> = {
  per_school: "Each school separately",
  platform: "Platform total",
};

export const COMPARISON_LABELS: Record<Comparison, string> = { gt: "Above", lt: "Below" };

export interface AlertRuleInput {
  name: string;
  metric_name: string;
  aggregation: Aggregation;
  filters: Record<string, string>;
  comparison: Comparison;
  threshold: number;
  for_minutes: number;
  scope: AlertScope;
  enabled: boolean;
}

export interface AlertTargetState {
  /** Null is the platform total. */
  school: TelemetrySchoolRef | null;
  state: AlertState;
  last_value: number | null;
  clear_streak: number;
  evaluated_at: string;
}

export interface AlertIncident {
  id: number;
  rule: { id: number; name: string; metric_name: string };
  unit: string;
  school: TelemetrySchoolRef | null;
  started_at: string;
  resolved_at: string | null;
  peak_value: number;
  last_value: number;
}

export interface AlertRuleRow extends AlertRuleInput {
  id: number;
  unit: string;
  /** "p95 of http.server.duration > 2000 ms for 10 min". */
  description: string;
  created_by: AdminRef | null;
  created_at: string;
  updated_at: string;
  states: AlertTargetState[];
  open_incidents: number;
}

export interface AlertPreview {
  step: number;
  series: MetricLine[];
  missing: number[];
  dropped: number[];
  unit: string;
  schools: Record<string, TelemetrySchoolRef>;
  /** The minutes an incident would have opened. */
  fired: Array<{ school: number | null; minute: number; value: number }>;
}

const ALERTS = [...TELEMETRY, "alerts"] as const;

export function useAlertRules() {
  return useQuery({
    queryKey: [...ALERTS, "rules"],
    queryFn: async () =>
      (await platformApi.get<{ results: AlertRuleRow[] }>("/observability/alert-rules/")).data.results,
    refetchInterval: PANELS_REFRESH_MS,
  });
}

function useAlertMutation<I, O>(fn: (input: I) => Promise<O>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ALERTS }),
  });
}

export const useCreateAlertRule = () =>
  useAlertMutation(
    async (input: AlertRuleInput) =>
      (await platformApi.post<AlertRuleRow>("/observability/alert-rules/", input)).data,
  );

export const useUpdateAlertRule = () =>
  useAlertMutation(
    async ({ id, ...input }: Partial<AlertRuleInput> & { id: number }) =>
      (await platformApi.patch<AlertRuleRow>(`/observability/alert-rules/${id}/`, input)).data,
  );

export const useDeleteAlertRule = () =>
  useAlertMutation((id: number) => platformApi.delete(`/observability/alert-rules/${id}/`));

/** The last 6 hours of an unsaved rule; null while the form isn't complete. */
export function useAlertPreview(input: Omit<AlertRuleInput, "name" | "enabled"> | null) {
  return useQuery({
    queryKey: [...ALERTS, "preview", input],
    queryFn: async () =>
      (await platformApi.post<AlertPreview>("/observability/alert-rules/preview/", input)).data,
    enabled: input !== null,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

export function useIncidents(cursor: string | null) {
  return useQuery({
    queryKey: [...ALERTS, "incidents", cursor],
    queryFn: async () =>
      (
        await platformApi.get<{ results: AlertIncident[]; next: string | null }>("/observability/incidents/", {
          params: { cursor: cursor ?? undefined },
        })
      ).data,
    placeholderData: keepPreviousData,
    refetchInterval: PANELS_REFRESH_MS,
  });
}
