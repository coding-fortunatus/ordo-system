import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { AxiosError } from "axios";
import { api } from "@/lib/api";
import type { PaginatedResponse } from "./types";

/** Portal sync (spec 0005): /api/portal-sync/. SA and DO only. */

export type ConnectionMode = "combined" | "per_type";
export type AuthType = "api_key_header" | "bearer";
export type EndpointKind = "combined" | "departments" | "classes" | "courses" | "students";

export interface PortalEndpoint {
  id: number;
  kind: EndpointKind;
  url: string;
  format: "json" | "csv";
  rows_path: string;
  pagination: "none" | "page_param" | "next_link";
  page_param: string;
  next_path: string;
  /** { ordo_field: their_column } */
  field_mapping: Record<string, string>;
  is_enabled: boolean;
}

export interface PortalConnection {
  id: number;
  mode: ConnectionMode;
  auth_type: AuthType;
  header_name: string;
  has_secret: boolean;
  secret_last4: string;
  approved: boolean;
  first_sync_approved_at: string | null;
  endpoints: PortalEndpoint[];
  created_at: string;
  updated_at: string;
}

export interface ConnectionInput {
  mode?: ConnectionMode;
  auth_type?: AuthType;
  header_name?: string;
  /** Write only. Leave out on update to keep the stored key. */
  secret?: string;
}

export interface OrdoField {
  key: string;
  label: string;
  required: boolean;
}

export type OrdoFields = Record<EndpointKind, OrdoField[]>;

export interface EndpointTestResult {
  columns: string[];
  sample_rows: Array<Record<string, string>>;
  row_count_on_page: number;
}

export type RunStatus =
  | "running"
  | "awaiting_approval"
  | "approved"
  | "superseded"
  | "succeeded"
  | "partial"
  | "held"
  | "failed";

export type DataType = "faculties" | "departments" | "classes" | "courses" | "students";

export type CountBucket = "created" | "linked" | "updated" | "deleted" | "skipped" | "held";

export interface TypeCounts extends Record<CountBucket, number> {
  type_status: "applied" | "held" | "not_synced";
  /** Previews only: up to 50 references per bucket (matric no, course code, or name). */
  examples?: Partial<Record<CountBucket, string[]>>;
}

export interface SyncRun {
  id: number;
  /** Null once its connection was deleted (AC-17): kept as history only. */
  connection: number | null;
  trigger: "manual" | "approval" | "confirm";
  is_preview: boolean;
  status: RunStatus;
  counts: Partial<Record<DataType, TypeCounts>>;
  error: string;
  allow_mass_delete: DataType[];
  started_by_email: string | null;
  job_id: string | null;
  issue_count: number;
  started_at: string;
  finished_at: string | null;
}

export type IssueKind =
  | "bad_row"
  | "delete_held_referenced"
  | "mass_delete_held"
  | "plan_limit"
  | "fetch_error";

export interface SyncIssue {
  id: number;
  data_type: DataType;
  kind: IssueKind;
  row_ref: string;
  message: string;
  target_id: number | null;
  resolution: "" | "delete" | "keep";
  resolved_by_email: string | null;
  resolved_at: string | null;
}

const KEY = ["portal-sync"] as const;
const CONNECTION_KEY = [...KEY, "connection"] as const;
const RUNS_KEY = [...KEY, "runs"] as const;

/** The connection, or null when the school hasn't set one up (404). */
export function usePortalConnection() {
  return useQuery({
    queryKey: CONNECTION_KEY,
    queryFn: async () => {
      try {
        return (await api.get<PortalConnection>("/portal-sync/connection/")).data;
      } catch (err) {
        if (err instanceof AxiosError && err.response?.status === 404) return null;
        throw err;
      }
    },
  });
}

export function useSaveConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: ConnectionInput) =>
      (await api.put<PortalConnection>("/portal-sync/connection/", data)).data,
    onSuccess: (data) => qc.setQueryData(CONNECTION_KEY, data),
  });
}

/** Disconnect (AC-17). Synced records become Ordo owned and editable
 *  again, so every list and badge refreshes; run history stays. */
export function useDeleteConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.delete("/portal-sync/connection/");
    },
    onSuccess: () => {
      qc.setQueryData(CONNECTION_KEY, null);
      return qc.invalidateQueries();
    },
  });
}

export function useOrdoFields() {
  return useQuery({
    queryKey: [...KEY, "fields"],
    queryFn: async () => (await api.get<OrdoFields>("/portal-sync/fields/")).data,
    staleTime: Infinity,
  });
}

export function useAddEndpoint() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: Partial<PortalEndpoint>) =>
      (await api.post<PortalEndpoint>("/portal-sync/endpoints/", data)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: CONNECTION_KEY }),
  });
}

export function useUpdateEndpoint() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...data }: Partial<PortalEndpoint> & { id: number }) =>
      (await api.patch<PortalEndpoint>(`/portal-sync/endpoints/${id}/`, data)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: CONNECTION_KEY }),
  });
}

export function useDeleteEndpoint() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/portal-sync/endpoints/${id}/`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: CONNECTION_KEY }),
  });
}

/** First page only; nothing is stored. A failure is 422 with a `reason`. */
export function useTestEndpoint() {
  return useMutation({
    mutationFn: async (id: number) =>
      (await api.post<EndpointTestResult>(`/portal-sync/endpoints/${id}/test/`)).data,
  });
}

export function useSyncRuns(page: number) {
  return useQuery({
    queryKey: [...RUNS_KEY, { page }],
    queryFn: async () =>
      (await api.get<PaginatedResponse<SyncRun>>("/portal-sync/runs/", { params: { page } }))
        .data,
    placeholderData: keepPreviousData,
  });
}

export function useSyncRun(id: number | null) {
  return useQuery({
    queryKey: [...RUNS_KEY, id],
    queryFn: async () => (await api.get<SyncRun>(`/portal-sync/runs/${id}/`)).data,
    enabled: id !== null,
    // A running sync finishes on its own: keep the detail fresh until then.
    refetchInterval: (query) => (query.state.data?.status === "running" ? 3000 : false),
  });
}

export function useSyncIssues(runId: number | null, page: number, kind?: IssueKind) {
  return useQuery({
    queryKey: [...RUNS_KEY, runId, "issues", { page, kind }],
    queryFn: async () =>
      (
        await api.get<PaginatedResponse<SyncIssue>>(`/portal-sync/runs/${runId}/issues/`, {
          params: { page, kind },
        })
      ).data,
    enabled: runId !== null,
    placeholderData: keepPreviousData,
  });
}

export interface StartedRun {
  run_id: number;
  job_id: string;
  is_preview: boolean;
}

/** Until the first sync is approved, this starts a preview that applies nothing. */
export function useStartSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.post<StartedRun>("/portal-sync/runs/")).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: RUNS_KEY }),
  });
}

/** Approve a preview: it becomes `approved` and a real sync starts (AC-4). */
export function useApproveRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (runId: number) =>
      (await api.post<StartedRun>(`/portal-sync/runs/${runId}/approve/`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

/** Start a sync allowed past the mass delete hold for `dataTypes` (AC-11).
 *  Only the latest run can be confirmed (409 otherwise). */
export function useConfirmMassDelete() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ runId, dataTypes }: { runId: number; dataTypes: DataType[] }) =>
      (
        await api.post<StartedRun>(`/portal-sync/runs/${runId}/confirm-mass-delete/`, {
          data_types: dataTypes,
        })
      ).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: RUNS_KEY }),
  });
}

/** Delete or keep a record whose delete was held because it is in use (AC-10). */
export function useResolveIssue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, resolution }: { id: number; resolution: "delete" | "keep" }) =>
      (await api.post<SyncIssue>(`/portal-sync/issues/${id}/resolve/`, { resolution })).data,
    // The record changed too: a deleted student leaves its lists and counts.
    onSuccess: () => qc.invalidateQueries(),
  });
}

/** Refresh everything a finished sync may have changed. */
export function useSyncFinished() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries();
}
