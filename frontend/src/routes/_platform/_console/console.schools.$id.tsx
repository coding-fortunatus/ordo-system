import { createFileRoute } from "@tanstack/react-router";
import type { MetricRange } from "@/api/telemetry";
import { str } from "@/lib/search";
import ConsoleSchoolDetailPage, { type SchoolSearch } from "@/pages/platform/school-detail";
import NotFoundPage from "@/pages/not-found";
import { RANGES } from "./console.observability.index";

export const Route = createFileRoute("/_platform/_console/console/schools/$id")({
  validateSearch: (search: Record<string, unknown>): SchoolSearch => {
    const range = str(search.range);
    return {
      tab: search.tab === "observability" ? "observability" : undefined,
      range: range && RANGES.includes(range) ? (range as MetricRange) : undefined,
    };
  },
  head: () => ({ meta: [{ title: "School | Ordo console" }] }),
  component: SchoolRoute,
});

function SchoolRoute() {
  const { id } = Route.useParams();
  const search = Route.useSearch();
  const pk = Number(id);
  if (!Number.isInteger(pk) || pk <= 0) return <NotFoundPage />;
  return <ConsoleSchoolDetailPage key={pk} id={pk} search={search} />;
}
