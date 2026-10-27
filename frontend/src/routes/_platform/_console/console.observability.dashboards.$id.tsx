import { createFileRoute } from "@tanstack/react-router";
import type { MetricRange } from "@/api/telemetry";
import { str } from "@/lib/search";
import NotFoundPage from "@/pages/not-found";
import { ConsoleDashboardPage, type DashboardSearch } from "@/pages/platform/dashboards";
import { RANGES } from "./console.observability.index";

export const Route = createFileRoute("/_platform/_console/console/observability/dashboards/$id")({
  validateSearch: (search: Record<string, unknown>): DashboardSearch => {
    const range = str(search.range);
    const school = Number(str(search.school));
    return {
      range: range && RANGES.includes(range) ? (range as MetricRange) : undefined,
      school: Number.isInteger(school) && school > 0 ? school : undefined,
    };
  },
  head: () => ({ meta: [{ title: "Dashboard | Ordo console" }] }),
  component: DashboardRoute,
});

function DashboardRoute() {
  const { id } = Route.useParams();
  const search = Route.useSearch();
  const pk = Number(id);
  if (!Number.isInteger(pk) || pk <= 0) return <NotFoundPage />;
  return <ConsoleDashboardPage key={pk} id={pk} search={search} />;
}
