import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AxiosError, type AxiosResponse } from "axios";
import type { PlanRow } from "@/api/platform";
import { platformApi } from "@/lib/api";
import { usePlatformAuth } from "@/lib/platform-auth";
import { ok, renderWithQuery } from "@/test/render";
import ConsolePlansPage from "./plans";

vi.mock("@/lib/platform-auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/platform-auth")>()),
  usePlatformAuth: vi.fn(),
}));

const STARTER: PlanRow = {
  id: 1,
  code: "starter",
  name: "Starter",
  max_students: 2000,
  max_staff_users: null,
  features: ["distribution", "exports", "seat_allocation"],
  is_active: true,
  school_count: 3,
};

function signedInAs(role: "owner" | "support") {
  vi.mocked(usePlatformAuth).mockReturnValue({
    admin: { id: 1, email: "a@ordo.app", name: "A", role },
  } as ReturnType<typeof usePlatformAuth>);
}

describe("ConsolePlansPage", () => {
  beforeEach(() => {
    vi.spyOn(platformApi, "get").mockResolvedValue(ok([STARTER]));
  });

  it("lists each plan with its limits, features and school count", async () => {
    signedInAs("support");

    renderWithQuery(<ConsolePlansPage />);

    const row = (await screen.findByText("Starter")).closest("tr")!;
    expect(within(row).getByText("2,000")).toBeInTheDocument();
    expect(within(row).getByText("Unlimited")).toBeInTheDocument();
    expect(within(row).getByText("Distribution, Exports, Seat allocation")).toBeInTheDocument();
    expect(within(row).getByText("3")).toBeInTheDocument();
    expect(within(row).getByText("Offered")).toBeInTheDocument();
  });

  it("gives Support no way to change plans", async () => {
    signedInAs("support");

    renderWithQuery(<ConsolePlansPage />);

    await screen.findByText("Starter");
    expect(screen.queryByRole("button", { name: /new plan/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Starter" })).not.toBeInTheDocument();
  });

  it("lets an Owner create a plan, sending blank limits as unlimited", async () => {
    signedInAs("owner");
    const post = vi.spyOn(platformApi, "post").mockResolvedValue(ok({ ...STARTER, id: 2 }));
    const user = userEvent.setup();
    renderWithQuery(<ConsolePlansPage />);

    await user.click(await screen.findByRole("button", { name: /new plan/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Name"), "Pro");
    await user.type(within(dialog).getByLabelText("Code"), "PRO");
    await user.type(within(dialog).getByLabelText("Student limit"), "5000");
    await user.click(within(dialog).getByLabelText("Exports"));
    await user.click(within(dialog).getByRole("button", { name: "Create plan" }));

    await vi.waitFor(() =>
      expect(post).toHaveBeenCalledWith("/plans/", {
        code: "pro",
        name: "Pro",
        max_students: 5000,
        max_staff_users: null,
        features: ["exports"],
        is_active: true,
      }),
    );
  });

  it("checks a limit is a whole number before sending", async () => {
    signedInAs("owner");
    const post = vi.spyOn(platformApi, "post");
    const user = userEvent.setup();
    renderWithQuery(<ConsolePlansPage />);

    await user.click(await screen.findByRole("button", { name: /new plan/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Name"), "Pro");
    await user.type(within(dialog).getByLabelText("Code"), "pro");
    await user.type(within(dialog).getByLabelText("Student limit"), "-5");
    await user.click(within(dialog).getByRole("button", { name: "Create plan" }));

    expect(await within(dialog).findByText(/whole number/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("shows the server's field errors on the form", async () => {
    signedInAs("owner");
    vi.spyOn(platformApi, "post").mockRejectedValue(
      new AxiosError("Bad Request", "ERR_BAD_REQUEST", undefined, undefined, {
        ...ok({ detail: "Validation failed.", errors: { code: ["plan with this code already exists."] } }),
        status: 400,
        statusText: "Bad Request",
      } as AxiosResponse),
    );
    const user = userEvent.setup();
    renderWithQuery(<ConsolePlansPage />);

    await user.click(await screen.findByRole("button", { name: /new plan/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Name"), "Again");
    await user.type(within(dialog).getByLabelText("Code"), "starter");
    await user.click(within(dialog).getByRole("button", { name: "Create plan" }));

    expect(await within(dialog).findByText("plan with this code already exists.")).toBeInTheDocument();
  });

  it("edits a plan without sending its code, which can't change", async () => {
    signedInAs("owner");
    const patch = vi.spyOn(platformApi, "patch").mockResolvedValue(ok(STARTER));
    const user = userEvent.setup();
    renderWithQuery(<ConsolePlansPage />);

    await user.click(await screen.findByRole("button", { name: "Edit Starter" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Code")).toBeDisabled();
    const limit = within(dialog).getByLabelText("Student limit");
    await user.clear(limit);
    await user.click(within(dialog).getByRole("switch"));
    await user.click(within(dialog).getByRole("button", { name: "Save plan" }));

    await vi.waitFor(() =>
      expect(patch).toHaveBeenCalledWith("/plans/1/", {
        name: "Starter",
        max_students: null,
        max_staff_users: null,
        features: ["distribution", "exports", "seat_allocation"],
        is_active: false,
      }),
    );
  });
});
