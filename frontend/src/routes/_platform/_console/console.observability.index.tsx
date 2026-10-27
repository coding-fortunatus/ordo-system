import { createFileRoute } from "@tanstack/react-router";
import type { MetricRange } from "@/api/telemetry";
import { str } from "@/lib/search";
import ConsoleObservabilityPage, { type ObservabilitySearch } from "@/pages/platform/observability";

export const RANGES = ["1h", "6h", "24h", "7d", "30d", "90d"];

export const Route = createFileRoute("/_platform/_console/console/observability/")({
  validateSearch: (search: Record<string, unknown>): ObservabilitySearch => {
    const range = str(search.range);
    const school = Number(str(search.school));
    return {
      range: range && RANGES.includes(range) ? (range as MetricRange) : undefined,
      school: Number.isInteger(school) && school > 0 ? school : undefined,
    };
  },
  head: () => ({ meta: [{ title: "Observability | Ordo console" }] }),
  component: ObservabilityRoute,
});

function ObservabilityRoute() {
  const search = Route.useSearch();
  return <ConsoleObservabilityPage search={search} />;
}
