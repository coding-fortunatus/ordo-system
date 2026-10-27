import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { platformApi } from "@/lib/api";
import type { SchoolStatus } from "./platform";
import type { PaginatedResponse } from "./types";

/** Error observability in the console (spec 0004). */

export type HealthStatus = "healthy" | "degraded" | "failing";
export type ErrorGroupStatus = "open" | "resolved" | "ignored";
export type ErrorSource = "api" | "task" | "job" | "log" | "js" | "ws";
export type ErrorKind = "error" | "expected";

export const HEALTH_LABELS: Record<HealthStatus, string> = {
  healthy: "Healthy",
  degraded: "Degraded",
  failing: "Failing",
};

export const GROUP_STATUS_LABELS: Record<ErrorGroupStatus, string> = {
  open: "Open",
  resolved: "Resolved",
  ignored: "Ignored",
};

export const SOURCE_LABELS: Record<ErrorSource, string> = {
  api: "API request",
  task: "Task",
  job: "Job",
  log: "Log",
  js: "Browser",
  ws: "WebSocket",
};

export const KIND_LABELS: Record<ErrorKind, string> = {
  error: "Error",
  expected: "Expected",
};

export interface HealthCounts {
  server_errors: number;
  client_errors: number;
  ws_errors: number;
  js_errors: number;
  job_failures: number;
  requests: number;
}

/** Null health means status hasn't been computed yet. */
export interface HealthFields extends HealthCounts {
  health: HealthStatus | null;
  reason: string;
  computed_at: string | null;
}

export interface HealthSchoolRef {
  id: number;
  name: string;
  slug: string;
  status: SchoolStatus;
}

export interface HealthRow extends HealthFields {
  /** Null is Platform: the console, beat tasks, email and backups. */
  school: HealthSchoolRef | null;
  open_groups: number;
}

export interface HealthBucket {
  hour: string;
  requests: number;
  server_errors: number;
  client_errors: number;
  ws_errors: number;
  job_failures: number;
  expected_job_failures: number;
  js_errors: number;
}

export interface HealthDetail extends HealthFields {
  buckets: HealthBucket[];
}

export interface ErrorGroup {
  id: number;
  title: string;
  culprit: string;
  exc_type: string;
  source: ErrorSource;
  kind: ErrorKind;
  status: ErrorGroupStatus;
  count: number;
  first_seen: string;
  last_seen: string;
  last_release: string;
}

export interface ErrorGroupDetail extends ErrorGroup {
  school: HealthSchoolRef | null;
  last_opened_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
}

export interface StackFrame {
  file: string;
  line: number;
  function: string;
  code: string;
}

/** Only these keys are ever stored (no bodies, queries, headers or cookies). */
export interface ErrorContext {
  method?: string;
  route?: string;
  status?: number;
  user_id?: number;
  role?: string;
  support?: boolean;
  request_id?: string;
  job_id?: string;
  task_name?: string;
  page_path?: string;
  user_agent?: string;
}

export interface ErrorEvent {
  id: number;
  occurred_at: string;
  message: string;
  stack: StackFrame[];
  context: ErrorContext;
  release: string;
  /** The request's or job's trace (spec 0006); null when tracing was off. */
  trace_id: string | null;
  /** Whether the trace and its logs are still stored, so the links open. */
  trace_kept: boolean;
  logs_kept: boolean;
}

/** A school's id, or "platform". */
export type ErrorScope = number | "platform";

export interface ErrorGroupParams {
  status?: ErrorGroupStatus;
  source?: ErrorSource;
  kind?: ErrorKind;
  page?: number;
}

const OBS = ["platform", "observability"] as const;
// Status is recomputed every 5 minutes; the page checks every minute.
const REFRESH = 60_000;

export function useHealthList(page = 1) {
  return useQuery({
    queryKey: [...OBS, "health", page],
    queryFn: async () =>
      (await platformApi.get<PaginatedResponse<HealthRow>>("/health/", { params: { page } })).data,
    placeholderData: keepPreviousData,
    refetchInterval: REFRESH,
  });
}

export function useScopeHealth(scope: ErrorScope) {
  return useQuery({
    queryKey: [...OBS, "health", "detail", scope],
    queryFn: async () =>
      (
        await platformApi.get<HealthDetail>(
          scope === "platform" ? "/health/platform/" : `/schools/${scope}/health/`,
        )
      ).data,
    refetchInterval: REFRESH,
  });
}

export function useErrorGroups(scope: ErrorScope, params: ErrorGroupParams) {
  return useQuery({
    queryKey: [...OBS, "groups", scope, params],
    queryFn: async () =>
      (
        await platformApi.get<PaginatedResponse<ErrorGroup>>("/errors/", {
          params: { school: scope, ...params },
        })
      ).data,
    placeholderData: keepPreviousData,
    refetchInterval: REFRESH,
  });
}

export function useErrorGroup(id: number | null) {
  return useQuery({
    queryKey: [...OBS, "group", id],
    enabled: id !== null,
    queryFn: async () => (await platformApi.get<ErrorGroupDetail>(`/errors/${id}/`)).data,
  });
}

export function useErrorEvents(id: number | null, page = 1) {
  return useQuery({
    queryKey: [...OBS, "events", id, page],
    enabled: id !== null,
    queryFn: async () =>
      (
        await platformApi.get<PaginatedResponse<ErrorEvent>>(`/errors/${id}/events/`, {
          params: { page },
        })
      ).data,
    placeholderData: keepPreviousData,
  });
}

/** Resolve, ignore or reopen a group. Audited on the server. */
export function useSetErrorGroupStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, status }: { id: number; status: ErrorGroupStatus }) =>
      (await platformApi.post<ErrorGroupDetail>(`/errors/${id}/status/`, { status })).data,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: OBS });
      void qc.invalidateQueries({ queryKey: ["platform", "audit"] });
    },
  });
}
