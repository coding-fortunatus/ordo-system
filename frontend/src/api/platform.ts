import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { platformApi } from "@/lib/api";
import type { PlatformRole } from "@/lib/platform-auth";
import type { FeatureKey } from "./school";
import type { PaginatedResponse } from "./types";

export interface PlatformAdminRow {
  id: number;
  email: string;
  name: string;
  role: PlatformRole;
  is_active: boolean;
  totp_enrolled: boolean;
  invite_pending: boolean;
  last_login: string | null;
  created_at: string;
}

export interface InviteAdminInput {
  email: string;
  name: string;
  role: PlatformRole;
}

export type UpdateAdminInput = Partial<
  Pick<PlatformAdminRow, "name" | "role" | "is_active">
>;

const ADMINS = ["platform", "admins"] as const;
const AUDIT = ["platform", "audit"] as const;

export function usePlatformAdmins({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ADMINS,
    enabled,
    queryFn: async () =>
      (await platformApi.get<PlatformAdminRow[]>("/admins/")).data,
  });
}

/** Invalidate the admins list and the audit log after any admin change. */
function useAdminMutation<TVars>(fn: (vars: TVars) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ADMINS });
      void qc.invalidateQueries({ queryKey: AUDIT });
    },
  });
}

export const useInviteAdmin = () =>
  useAdminMutation((input: InviteAdminInput) =>
    platformApi.post<PlatformAdminRow>("/admins/", input),
  );

export const useUpdateAdmin = () =>
  useAdminMutation(({ id, ...input }: UpdateAdminInput & { id: number }) =>
    platformApi.patch<PlatformAdminRow>(`/admins/${id}/`, input),
  );

export const useResetAdmin2fa = () =>
  useAdminMutation((id: number) => platformApi.post(`/admins/${id}/reset-2fa/`));

export const useUnlockAdmin = () =>
  useAdminMutation((id: number) => platformApi.post(`/admins/${id}/unlock/`));

export const useResendAdminInvite = () =>
  useAdminMutation((id: number) =>
    platformApi.post(`/admins/${id}/resend-invite/`),
  );

/** Set a password from an emailed invite link. No session needed. */
export async function acceptAdminInvite(token: string, password: string) {
  await platformApi.post("/admins/accept/", { token, password });
}

export interface PlatformAuditRow {
  id: number;
  admin: { id: number; name: string; email: string } | null;
  school: { id: number; name: string; slug: string } | null;
  action: string;
  detail: Record<string, unknown>;
  ip: string | null;
  created_at: string;
}

export interface PlatformAuditParams {
  page?: number;
  action?: string;
  admin?: number;
  school?: number;
}

export function usePlatformAudit(params: PlatformAuditParams = {}) {
  return useQuery({
    queryKey: [...AUDIT, params],
    queryFn: async () =>
      (
        await platformApi.get<PaginatedResponse<PlatformAuditRow>>("/audit/", {
          params,
        })
      ).data,
    placeholderData: keepPreviousData,
  });
}

export { FEATURE_LABELS, type FeatureKey } from "./school";

export interface PlanRow {
  id: number;
  code: string;
  name: string;
  max_students: number | null;
  max_staff_users: number | null;
  features: FeatureKey[];
  is_active: boolean;
  /** Schools on this plan. */
  school_count: number;
}

export type PlanInput = Pick<
  PlanRow,
  "code" | "name" | "max_students" | "max_staff_users" | "features" | "is_active"
>;

const PLANS = ["platform", "plans"] as const;

export function usePlans() {
  return useQuery({
    queryKey: PLANS,
    queryFn: async () => (await platformApi.get<PlanRow[]>("/plans/")).data,
  });
}

/** Invalidate plans, schools (they show their plan) and the audit log. */
function usePlanMutation<TVars>(fn: (vars: TVars) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PLANS });
      void qc.invalidateQueries({ queryKey: SCHOOLS });
      void qc.invalidateQueries({ queryKey: AUDIT });
    },
  });
}

export const useCreatePlan = () =>
  usePlanMutation((input: PlanInput) => platformApi.post<PlanRow>("/plans/", input));

/** Any field but the code, which never changes. Plans are deactivated, never deleted. */
export const useUpdatePlan = () =>
  usePlanMutation(({ id, ...input }: Partial<Omit<PlanInput, "code">> & { id: number }) =>
    platformApi.patch<PlanRow>(`/plans/${id}/`, input),
  );

export type SchoolStatus =
  | "provisioning"
  | "active"
  | "failed"
  | "suspended"
  | "pending_deletion"
  | "purged";

export const SCHOOL_STATUS_LABELS: Record<SchoolStatus, string> = {
  provisioning: "Setting up",
  active: "Active",
  failed: "Setup failed",
  suspended: "Suspended",
  pending_deletion: "Pending deletion",
  purged: "Purged",
};

/** A school's usage as of its latest nightly snapshot. */
export interface SchoolUsage {
  taken_on: string;
  students: number;
  staff_users: number;
  jobs_last_24h: number;
  last_activity_at: string | null;
}

export interface SchoolRow {
  id: number;
  name: string;
  slug: string;
  status: SchoolStatus;
  plan: { id: number; code: string; name: string };
  domain: string | null;
  first_admin_name: string;
  first_admin_email: string;
  /** Null while the school has no live schema. */
  first_admin_joined: boolean | null;
  country: string;
  provisioning_error: string;
  created_at: string;
  /** Null until the first nightly snapshot. */
  usage: SchoolUsage | null;
}

export interface SchoolDetail extends SchoolRow {
  domains: { domain: string; is_primary: boolean }[];
  max_students_override: number | null;
  max_staff_users_override: number | null;
  features_override: FeatureKey[] | null;
  contact_name: string;
  contact_email: string;
  timezone: string;
  notes: string;
  plan_started_on: string | null;
  renews_on: string | null;
  provisioning_started_at: string | null;
  was_ever_active: boolean;
  suspended_at: string | null;
  suspended_reason: string;
  deletion_requested_at: string | null;
  /** The school is purged (and can no longer be restored) after this. */
  purge_after: string | null;
  /** Bucket key of the final export, "never_active", or blank while exporting. */
  final_export_ref: string;
  purged_at: string | null;
  created_by: { id: number; name: string; email: string } | null;
  /** Null while the school has no live schema yet. */
  first_admin: { joined: boolean; invite_expires_at: string | null } | null;
}

export interface CreateSchoolInput {
  name: string;
  slug: string;
  plan: string;
  max_students_override?: number | null;
  max_staff_users_override?: number | null;
  features_override?: FeatureKey[] | null;
  first_admin_name: string;
  first_admin_email: string;
  contact_name?: string;
  contact_email?: string;
  country?: string;
  timezone?: string;
  notes?: string;
  plan_started_on?: string | null;
  renews_on?: string | null;
}

export interface SchoolListParams {
  page?: number;
  status?: SchoolStatus;
  q?: string;
}

const SCHOOLS = ["platform", "schools"] as const;

/** Poll while a school is being set up, so its status flips on its own. */
const settling = (statuses: SchoolStatus[]) =>
  statuses.includes("provisioning") ? 3000 : false;

export function useSchools(params: SchoolListParams = {}) {
  return useQuery({
    queryKey: [...SCHOOLS, "list", params],
    queryFn: async () =>
      (
        await platformApi.get<PaginatedResponse<SchoolRow>>("/schools/", {
          params,
        })
      ).data,
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      settling(query.state.data?.results.map((s) => s.status) ?? []),
  });
}

export function useSchool(id: number) {
  return useQuery({
    queryKey: [...SCHOOLS, "detail", id],
    queryFn: async () =>
      (await platformApi.get<SchoolDetail>(`/schools/${id}/`)).data,
    refetchInterval: (query) =>
      settling(query.state.data ? [query.state.data.status] : []),
  });
}

/** Invalidate schools and the audit log after any school change. */
function useSchoolMutation<TVars, TData>(fn: (vars: TVars) => Promise<TData>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SCHOOLS });
      void qc.invalidateQueries({ queryKey: AUDIT });
    },
  });
}

export const useCreateSchool = () =>
  useSchoolMutation(
    async (input: CreateSchoolInput) =>
      (await platformApi.post<SchoolDetail>("/schools/", input)).data,
  );

/** Any create field but the slug, which never changes. */
export type UpdateSchoolInput = Partial<Omit<CreateSchoolInput, "slug">>;

export const useUpdateSchool = () =>
  useSchoolMutation(
    async ({ id, ...input }: UpdateSchoolInput & { id: number }) =>
      (await platformApi.patch<SchoolDetail>(`/schools/${id}/`, input)).data,
  );

export const useRetrySchool = () =>
  useSchoolMutation((id: number) => platformApi.post(`/schools/${id}/retry/`));

export const useResendSchoolInvite = () =>
  useSchoolMutation((id: number) =>
    platformApi.post(`/schools/${id}/resend-invite/`),
  );

export const useSuspendSchool = () =>
  useSchoolMutation(({ id, reason }: { id: number; reason: string }) =>
    platformApi.post(`/schools/${id}/suspend/`, { reason }),
  );

export const useReactivateSchool = () =>
  useSchoolMutation((id: number) => platformApi.post(`/schools/${id}/reactivate/`));

export const useDeleteSchool = () =>
  useSchoolMutation(({ id, confirm_slug }: { id: number; confirm_slug: string }) =>
    platformApi.post(`/schools/${id}/delete/`, { confirm_slug }),
  );

export const useRestoreSchool = () =>
  useSchoolMutation((id: number) => platformApi.post(`/schools/${id}/restore/`));

/** An Ordo support visit to a school (spec 0003, child 0006). */
export type SupportSessionState = "waiting" | "active" | "ended" | "unused";

export interface SupportSessionRow {
  id: number;
  admin: { id: number; name: string; email: string };
  reason: string;
  state: SupportSessionState;
  created_at: string;
  ticket_expires_at: string;
  started_at: string | null;
  expires_at: string | null;
  ended_at: string | null;
}

export interface StartedSupportSession {
  session_id: number;
  /** One time link to the school's /support page, valid for 60 seconds. */
  url: string;
  ticket_expires_at: string;
}

export function useSupportSessions(schoolId: number) {
  return useQuery({
    queryKey: [...SCHOOLS, "support", schoolId],
    queryFn: async () =>
      (await platformApi.get<SupportSessionRow[]>(`/schools/${schoolId}/support-sessions/`)).data,
  });
}

export const useStartSupportSession = () =>
  useSchoolMutation(
    async ({ id, reason }: { id: number; reason: string }) =>
      (
        await platformApi.post<StartedSupportSession>(`/schools/${id}/support-sessions/`, {
          reason,
        })
      ).data,
  );

export const useEndSupportSession = () =>
  useSchoolMutation((sessionId: number) =>
    platformApi.post(`/support-sessions/${sessionId}/end/`),
  );

export type AttentionKind =
  | "failed"
  | "pending_deletion"
  | "suspended"
  | "renewal_overdue"
  | "renewal_due";

export interface PlatformOverview {
  schools: {
    /** Every school but purged ones. */
    total: number;
    by_status: Record<SchoolStatus, number>;
    new_last_30_days: number;
  };
  /** Each live school's latest nightly snapshot, added up. */
  usage: {
    students: number;
    staff_users: number;
    jobs_last_24h: number;
    as_of: string | null;
  };
  /** Daily totals over the last 30 days, oldest first. */
  trend: Array<{ date: string; students: number; staff_users: number; jobs: number }>;
  plans: Array<{ id: number; code: string; name: string; schools: number }>;
  attention: Array<{
    school: { id: number; name: string; slug: string };
    kind: AttentionKind;
    at: string | null;
  }>;
  support: Array<{
    id: number;
    school: { id: number; name: string; slug: string };
    admin: { id: number; name: string };
    reason: string;
    expires_at: string;
  }>;
  recent_activity: PlatformAuditRow[];
  backup: BackupSummary;
}

/** The console dashboard. Under the schools key so school changes refresh it. */
export function usePlatformOverview() {
  return useQuery({
    queryKey: [...SCHOOLS, "overview"],
    queryFn: async () =>
      (await platformApi.get<PlatformOverview>("/overview/")).data,
    refetchInterval: 60_000,
  });
}

/* ------------------------------------------------------------ backups */

export interface BackupSummary {
  configured: boolean;
  /** No verified backup in the last 26 hours. */
  stale: boolean;
  last_success: { started_at: string; size_bytes: number | null; key: string } | null;
  last_run: { started_at: string; ok: boolean; error: string } | null;
  /** Up to 14 attempts, oldest first. */
  recent: Array<{ started_at: string; ok: boolean; size_bytes: number | null }>;
}

/* ------------------------------------------------------------ account */

const SESSIONS = ["platform", "me", "sessions"] as const;

export interface PlatformSession {
  id: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  user_agent: string;
  ip: string | null;
  current: boolean;
}

export function useMySessions() {
  return useQuery({
    queryKey: SESSIONS,
    queryFn: async () => (await platformApi.get<PlatformSession[]>("/me/sessions/")).data,
  });
}

function useSessionMutation<TVars, TData = unknown>(fn: (vars: TVars) => Promise<TData>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SESSIONS });
      void qc.invalidateQueries({ queryKey: AUDIT });
    },
  });
}

export const useChangePassword = () =>
  useSessionMutation((input: { current_password: string; new_password: string }) =>
    platformApi.post("/me/password/", input),
  );

export const useRevokeSession = () =>
  useSessionMutation((id: string) => platformApi.delete(`/me/sessions/${id}/`));

export const useRevokeOtherSessions = () =>
  useSessionMutation(async () =>
    (await platformApi.post<{ ended: number }>("/me/sessions/revoke-others/")).data,
  );

export const useStartReenrol = () =>
  useMutation({
    mutationFn: async (input: { current_password: string; code: string }) =>
      (await platformApi.post<{ challenge: string; otpauth_uri: string }>("/me/2fa/", input)).data,
  });

export const useConfirmReenrol = () =>
  useSessionMutation((input: { challenge: string; code: string }) =>
    platformApi.post("/me/2fa/confirm/", input),
  );

export const useSignOutAdmin = () =>
  useAdminMutation(async (id: number) =>
    (await platformApi.post<{ ended: number }>(`/admins/${id}/sign-out/`)).data,
  );

/* ------------------------------------------------------------ usage */

export interface SchoolUsageHistory {
  limits: { max_students: number | null; max_staff_users: number | null };
  snapshots: Array<{
    date: string;
    students: number;
    staff_users: number;
    jobs: number;
    last_activity_at: string | null;
  }>;
}

export function useSchoolUsage(id: number, days = 90) {
  return useQuery({
    queryKey: [...SCHOOLS, "usage", id, days],
    queryFn: async () =>
      (await platformApi.get<SchoolUsageHistory>(`/schools/${id}/usage/`, { params: { days } })).data,
  });
}

/* ------------------------------------------------------------ staff search */

export interface StaffHit {
  email: string;
  name: string;
  role: string;
  is_active: boolean;
  last_login: string | null;
  school: { id: number; name: string; slug: string; status: SchoolStatus };
}

export function useStaffSearch(q: string) {
  const query = q.trim();
  return useQuery({
    queryKey: ["platform", "staff", query],
    queryFn: async () =>
      (
        await platformApi.get<{ indexed_at: string | null; results: StaffHit[] }>("/staff/", {
          params: { q: query },
        })
      ).data,
    enabled: query.length >= 3,
    placeholderData: keepPreviousData,
  });
}

export const useReindexStaff = () =>
  useMutation({ mutationFn: () => platformApi.post("/staff/reindex/") });

/* ------------------------------------------------------------ announcements */

const ANNOUNCEMENTS = ["platform", "announcements"] as const;

export type AnnouncementLevel = "info" | "warning";

export interface AnnouncementRow {
  id: number;
  message: string;
  level: AnnouncementLevel;
  starts_at: string;
  ends_at: string | null;
  withdrawn_at: string | null;
  created_by: { id: number; name: string } | null;
  created_at: string;
  state: "scheduled" | "showing" | "ended";
}

export interface AnnouncementInput {
  message: string;
  level: AnnouncementLevel;
  starts_at?: string;
  ends_at?: string | null;
}

export function useAnnouncements() {
  return useQuery({
    queryKey: ANNOUNCEMENTS,
    queryFn: async () => (await platformApi.get<AnnouncementRow[]>("/announcements/")).data,
  });
}

function useAnnouncementMutation<TVars>(fn: (vars: TVars) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ANNOUNCEMENTS });
      void qc.invalidateQueries({ queryKey: AUDIT });
    },
  });
}

export const useCreateAnnouncement = () =>
  useAnnouncementMutation((input: AnnouncementInput) => platformApi.post("/announcements/", input));

export const useWithdrawAnnouncement = () =>
  useAnnouncementMutation((id: number) => platformApi.post(`/announcements/${id}/withdraw/`));

/* ------------------------------------------------------------ demo requests */

const DEMO_REQUESTS = ["platform", "demo-requests"] as const;

export type DemoRequestKind = "demo" | "setup" | "question";
export type DemoRequestStatus = "new" | "contacted" | "scheduled" | "converted" | "closed";

export const DEMO_REQUEST_KINDS: Record<DemoRequestKind, string> = {
  demo: "Demo",
  setup: "Setup help",
  question: "Question",
};

export const DEMO_REQUEST_STATUSES: Record<DemoRequestStatus, string> = {
  new: "New",
  contacted: "Contacted",
  scheduled: "Scheduled",
  converted: "Converted",
  closed: "Closed",
};

export const STUDENT_COUNT_LABELS: Record<string, string> = {
  under_2k: "Under 2,000",
  "2k_10k": "2,000 – 10,000",
  "10k_30k": "10,000 – 30,000",
  over_30k: "Over 30,000",
};

export interface DemoRequestRow {
  id: number;
  kind: DemoRequestKind;
  name: string;
  email: string;
  phone: string;
  institution: string;
  job_title: string;
  student_count: string;
  message: string;
  status: DemoRequestStatus;
  notes: string;
  assigned_to: { id: number; name: string } | null;
  school: { id: number; name: string; slug: string } | null;
  created_at: string;
  updated_at: string;
}

export interface DemoRequestListParams {
  page?: number;
  /** A status, or "open" for every one still waiting on the team. */
  status?: DemoRequestStatus | "open";
  kind?: DemoRequestKind;
  q?: string;
}

export interface UpdateDemoRequestInput {
  id: number;
  status?: DemoRequestStatus;
  notes?: string;
  assigned_to_id?: number | null;
  school_id?: number | null;
}

export function useDemoRequests(params: DemoRequestListParams = {}) {
  return useQuery({
    queryKey: [...DEMO_REQUESTS, "list", params],
    queryFn: async () =>
      (
        await platformApi.get<PaginatedResponse<DemoRequestRow>>("/demo-requests/", { params })
      ).data,
    placeholderData: keepPreviousData,
  });
}

export function useDemoRequest(id: number | undefined) {
  return useQuery({
    queryKey: [...DEMO_REQUESTS, "detail", id],
    queryFn: async () => (await platformApi.get<DemoRequestRow>(`/demo-requests/${id}/`)).data,
    enabled: id !== undefined,
  });
}

/** Requests nobody has picked up yet, for the nav badge. */
export function useNewDemoRequestCount() {
  return useQuery({
    queryKey: [...DEMO_REQUESTS, "summary"],
    queryFn: async () =>
      (await platformApi.get<{ new: number }>("/demo-requests/summary/")).data.new,
    refetchInterval: 60_000,
  });
}

export function useUpdateDemoRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...input }: UpdateDemoRequestInput) =>
      (await platformApi.patch<DemoRequestRow>(`/demo-requests/${id}/`, input)).data,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: DEMO_REQUESTS });
      void qc.invalidateQueries({ queryKey: AUDIT });
    },
  });
}
