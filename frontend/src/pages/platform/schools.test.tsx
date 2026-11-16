import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import type { SchoolRow } from "@/api/platform";
import { platformApi } from "@/lib/api";
import { ConfirmProvider } from "@/lib/confirm";
import { usePlatformAuth } from "@/lib/platform-auth";
import { ok, renderWithQuery } from "@/test/render";
import ConsoleSchoolsPage from "./schools";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="#">{children}</a>,
  useNavigate: () => vi.fn(),
}));

vi.mock("@/lib/platform-auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/platform-auth")>()),
  usePlatformAuth: vi.fn(),
}));

const SCHOOL: SchoolRow = {
  id: 1,
  name: "University of Lagos",
  slug: "unilag",
  status: "active",
  plan: { id: 1, code: "starter", name: "Starter" },
  domain: "unilag.ordo.app",
  first_admin_name: "Ada Obi",
  first_admin_email: "ada@unilag.edu.ng",
  first_admin_joined: true,
  country: "NG",
  is_demo: false,
  provisioning_error: "",
  created_at: "2026-10-01T09:00:00Z",
  usage: {
    taken_on: "2026-10-11",
    students: 1240,
    staff_users: 6,
    jobs_last_24h: 1,
    last_activity_at: "2026-10-10T15:30:00Z",
  },
};

function renderPage() {
  return renderWithQuery(
    <ConfirmProvider>
      <ConsoleSchoolsPage />
    </ConfirmProvider>,
  );
}

function listing(...results: SchoolRow[]) {
  vi.spyOn(platformApi, "get").mockResolvedValue(
    ok({ count: results.length, next: null, previous: null, results }),
  );
}

describe("ConsoleSchoolsPage usage", () => {
  beforeEach(() => {
    vi.mocked(usePlatformAuth).mockReturnValue({
      admin: { id: 1, email: "a@ordo.app", name: "A", role: "support" },
    } as ReturnType<typeof usePlatformAuth>);
  });

  it("shows the latest snapshot's counts and last activity", async () => {
    listing(SCHOOL);

    renderPage();

    const row = (await screen.findByText("University of Lagos")).closest("tr")!;
    expect(within(row).getByText("1,240 students · 6 staff")).toBeInTheDocument();
    expect(within(row).getByText("1 job in 24h")).toBeInTheDocument();
    expect(
      within(row).getByText(new Date("2026-10-10T15:30:00Z").toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "2-digit",
      })),
    ).toBeInTheDocument();
  });

  it("says when a school has no snapshot yet, or no activity at all", async () => {
    listing(
      { ...SCHOOL, id: 2, name: "New School", usage: null },
      {
        ...SCHOOL,
        id: 3,
        name: "Quiet School",
        usage: { ...SCHOOL.usage!, jobs_last_24h: 0, last_activity_at: null },
      },
    );

    renderPage();

    const fresh = (await screen.findByText("New School")).closest("tr")!;
    expect(within(fresh).getAllByText("No data yet")).toHaveLength(2);
    const quiet = screen.getByText("Quiet School").closest("tr")!;
    expect(within(quiet).getByText("0 jobs in 24h")).toBeInTheDocument();
    expect(within(quiet).getByText("Never")).toBeInTheDocument();
  });
});
