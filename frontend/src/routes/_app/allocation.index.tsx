import { createFileRoute } from "@tanstack/react-router";
import { RequireCommittee, RequireFeature } from "@/components/route-guards";
import { str } from "@/lib/search";
import AllocationPage from "@/pages/allocation/page";

export const Route = createFileRoute("/_app/allocation/")({
  head: () => ({ meta: [{ title: "Seat allocation | Ordo" }] }),
  validateSearch: (search: Record<string, unknown>): { date?: string; period?: string } => ({
    date: str(search.date),
    period: str(search.period),
  }),
  component: () => (
    <RequireCommittee>
      <RequireFeature feature="seat_allocation">
        <AllocationPage />
      </RequireFeature>
    </RequireCommittee>
  ),
});
