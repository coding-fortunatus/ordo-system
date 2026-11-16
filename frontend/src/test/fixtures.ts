import type { SchoolPlan } from "@/api/school";

/** A school plan as GET /api/school/ returns it, for tests. */
export function schoolPlan(overrides: Partial<SchoolPlan> = {}): SchoolPlan {
  return {
    name: "Test School",
    slug: "test",
    status: "active",
    timezone: "Africa/Lagos",
    features: ["distribution", "seat_allocation", "exports", "publication", "branding"],
    limits: { max_students: null, max_staff_users: null },
    usage: { students: 0, staff_users: 1 },
    support: null,
    ...overrides,
  };
}
