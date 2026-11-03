import { createFileRoute } from "@tanstack/react-router";
import { RequireCommittee, RequireFeature } from "@/components/route-guards";
import { str } from "@/lib/search";
import HallAllocationPage from "@/pages/allocation/hall";

export const Route = createFileRoute("/_app/allocation/hall")({
  head: () => ({ meta: [{ title: "Hall allocation | Ordo" }] }),
  validateSearch: (search: Record<string, unknown>): { date?: string; period?: string; hall_id?: string } => ({
    date: str(search.date),
    period: str(search.period),
    hall_id: str(search.hall_id),
  }),
  component: () => (
    <RequireCommittee>
      <RequireFeature feature="seat_allocation">
        <HallAllocationPage />
      </RequireFeature>
    </RequireCommittee>
  ),
});
