import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** Optional features a plan switches on (tenancy.features.KEYS). */
export type FeatureKey =
  | "seat_allocation"
  | "exports"
  | "publication"
  | "branding"
  | "portal_sync";

export const FEATURE_LABELS: Record<FeatureKey, string> = {
  seat_allocation: "Seat allocation",
  exports: "Exports",
  publication: "Timetable publication",
  branding: "Branding",
  portal_sync: "Portal sync",
};

/** This school's effective plan: GET /api/school/. Null limits are unlimited. */
export interface SchoolPlan {
  name: string;
  slug: string;
  status: string;
  timezone: string;
  features: FeatureKey[];
  limits: { max_students: number | null; max_staff_users: number | null };
  usage: { students: number; staff_users: number };
  /** Set only for an Ordo support token: drives the support banner. */
  support: { active: boolean; expires_at: string; admin_name: string } | null;
  /** Messages from the Ordo team, newest first (at most three). */
  announcements?: Array<{ id: number; message: string; level: "info" | "warning"; ends_at: string | null }>;
}

export const SCHOOL_PLAN_KEY = ["school"] as const;

/** Loaded once by the dashboard layout; every other caller reads the cache. */
export function useSchoolPlan() {
  return useQuery({
    queryKey: SCHOOL_PLAN_KEY,
    queryFn: async () => (await api.get<SchoolPlan>("/school/")).data,
    staleTime: 5 * 60_000,
  });
}

/**
 * Whether the plan includes ``key``. True until the plan has loaded, so a
 * school with the feature never sees it flicker; the server refuses anyway.
 */
export function useHasFeature(key: FeatureKey): boolean {
  const plan = useSchoolPlan();
  return plan.data ? plan.data.features.includes(key) : true;
}

/** Whether the school is open: GET /api/school/status/, never refused by the status gate. */
export type SchoolStatus =
  | "provisioning"
  | "active"
  | "failed"
  | "suspended"
  | "pending_deletion"
  | "purged";

export interface SchoolStatusInfo {
  name: string;
  status: SchoolStatus;
}

export const SCHOOL_STATUS_KEY = ["school", "status"] as const;

export function useSchoolStatus() {
  return useQuery({
    queryKey: SCHOOL_STATUS_KEY,
    queryFn: async () => (await api.get<SchoolStatusInfo>("/school/status/")).data,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** Swap a console support ticket for a support token: POST /api/support/redeem/. */
export async function redeemSupportTicket(ticket: string) {
  return (
    await api.post<{ token: string; expires_at: string }>("/support/redeem/", { ticket })
  ).data;
}

/** End this support session now; the token stops working at once. */
export async function endSupportSession() {
  await api.post("/support/end/");
}
