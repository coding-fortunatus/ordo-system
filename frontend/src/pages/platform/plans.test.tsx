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
  price: 1500000,
  features: ["distribution", "exports", "seat_allocation"],
  is_active: true,
  school_count: 3,
  deletable: false,
};

const UNUSED: PlanRow = {
  ...STARTER,
  id: 2,
  code: "trial",
  name: "Trial",
  school_count: 0,
  deletable: true,
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

  it("lists each plan with its limits, price, features and school count", async () => {
    signedInAs("support");

    renderWithQuery(<ConsolePlansPage />);

    const row = (await screen.findByText("Starter")).closest("tr")!;
    expect(within(row).getByText("2,000")).toBeInTheDocument();
    expect(within(row).getByText("Unlimited")).toBeInTheDocument();
    expect(within(row).getByText("₦1,500,000")).toBeInTheDocument();
    expect(within(row).getByText("Distribution, Broadsheet and bulk attendance sheets, Seat allocation")).toBeInTheDocument();
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

  it("lets an Owner create a plan, sending blank limits as unlimited and a blank price as quoted", async () => {
    signedInAs("owner");
    const post = vi.spyOn(platformApi, "post").mockResolvedValue(ok({ ...STARTER, id: 2 }));
    const user = userEvent.setup();
    renderWithQuery(<ConsolePlansPage />);

    await user.click(await screen.findByRole("button", { name: /new plan/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Name"), "Pro");
    await user.type(within(dialog).getByLabelText("Code"), "PRO");
    await user.type(within(dialog).getByLabelText("Student limit"), "5000");
    await user.click(within(dialog).getByLabelText("Broadsheet and bulk attendance sheets"));
    await user.click(within(dialog).getByRole("button", { name: "Create plan" }));

    await vi.waitFor(() =>
      expect(post).toHaveBeenCalledWith("/plans/", {
        code: "pro",
        name: "Pro",
        max_students: 5000,
        max_staff_users: null,
        price: null,
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

  it("edits a plan's code, price and limits", async () => {
    signedInAs("owner");
    const patch = vi.spyOn(platformApi, "patch").mockResolvedValue(ok(STARTER));
    const user = userEvent.setup();
    renderWithQuery(<ConsolePlansPage />);

    await user.click(await screen.findByRole("button", { name: "Edit Starter" }));
    const dialog = await screen.findByRole("dialog");
    const code = within(dialog).getByLabelText("Code");
    await user.clear(code);
    await user.type(code, "Basic");
    const price = within(dialog).getByLabelText("Price per academic session (₦)");
    await user.clear(price);
    await user.type(price, "2000000");
    const limit = within(dialog).getByLabelText("Student limit");
    await user.clear(limit);
    await user.click(within(dialog).getByRole("switch"));
    await user.click(within(dialog).getByRole("button", { name: "Save plan" }));

    await vi.waitFor(() =>
      expect(patch).toHaveBeenCalledWith("/plans/1/", {
        code: "basic",
        name: "Starter",
        max_students: null,
        max_staff_users: null,
        price: 2000000,
        features: ["distribution", "exports", "seat_allocation"],
        is_active: false,
      }),
    );
  });

  it("archives a plan in use but offers no delete for it", async () => {
    signedInAs("owner");
    const patch = vi.spyOn(platformApi, "patch").mockResolvedValue(ok({ ...STARTER, is_active: false }));
    const user = userEvent.setup();
    renderWithQuery(<ConsolePlansPage />);

    await user.click(await screen.findByRole("button", { name: "Archive Starter" }));

    await vi.waitFor(() => expect(patch).toHaveBeenCalledWith("/plans/1/", { is_active: false }));
    expect(screen.queryByRole("button", { name: "Delete Starter" })).not.toBeInTheDocument();
  });

  it("restores an archived plan", async () => {
    signedInAs("owner");
    vi.spyOn(platformApi, "get").mockResolvedValue(ok([{ ...STARTER, is_active: false }]));
    const patch = vi.spyOn(platformApi, "patch").mockResolvedValue(ok(STARTER));
    const user = userEvent.setup();
    renderWithQuery(<ConsolePlansPage />);

    expect(await screen.findByText("Archived")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Restore Starter" }));

    await vi.waitFor(() => expect(patch).toHaveBeenCalledWith("/plans/1/", { is_active: true }));
  });

  it("deletes a plan no school uses after confirming", async () => {
    signedInAs("owner");
    vi.spyOn(platformApi, "get").mockResolvedValue(ok([STARTER, UNUSED]));
    const remove = vi.spyOn(platformApi, "delete").mockResolvedValue(ok(null));
    const user = userEvent.setup();
    renderWithQuery(<ConsolePlansPage />);

    await user.click(await screen.findByRole("button", { name: "Delete Trial" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete plan" }));

    await vi.waitFor(() => expect(remove).toHaveBeenCalledWith("/plans/2/"));
  });

  it("shows the server's refusal when a plan can't be deleted", async () => {
    signedInAs("owner");
    vi.spyOn(platformApi, "get").mockResolvedValue(ok([UNUSED]));
    vi.spyOn(platformApi, "delete").mockRejectedValue(
      new AxiosError("Conflict", "ERR_BAD_REQUEST", undefined, undefined, {
        ...ok({ detail: "1 school is on this plan, closed ones included. Archive it instead, or move them to another plan first." }),
        status: 409,
        statusText: "Conflict",
      } as AxiosResponse),
    );
    const user = userEvent.setup();
    renderWithQuery(<ConsolePlansPage />);

    await user.click(await screen.findByRole("button", { name: "Delete Trial" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete plan" }));

    expect(await within(dialog).findByText(/Archive it instead/)).toBeInTheDocument();
  });
});
