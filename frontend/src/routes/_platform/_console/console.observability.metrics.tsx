import { createFileRoute } from "@tanstack/react-router";
import type { Aggregation, MetricRange } from "@/api/telemetry";
import { str } from "@/lib/search";
import ConsoleMetricsPage, { type MetricsSearch } from "@/pages/platform/metrics";
import { RANGES } from "./console.observability.index";

/** Label filters as plain strings; the page drops any the metric doesn't take. */
function filters(value: unknown): Record<string, string> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const out = Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => [k, str(v)] as const)
      .filter((entry): entry is readonly [string, string] => entry[1] !== undefined),
  );
  return Object.keys(out).length ? out : undefined;
}

export const Route = createFileRoute("/_platform/_console/console/observability/metrics")({
  validateSearch: (search: Record<string, unknown>): MetricsSearch => {
    const range = str(search.range);
    const school = Number(str(search.school));
    return {
      metric: str(search.metric),
      // Checked against the metric on the page, once the registry is loaded.
      agg: str(search.agg) as Aggregation | undefined,
      group: str(search.group),
      filters: filters(search.filters),
      school: Number.isInteger(school) && school > 0 ? school : undefined,
      range: range && RANGES.includes(range) ? (range as MetricRange) : undefined,
    };
  },
  head: () => ({ meta: [{ title: "Metrics | Ordo console" }] }),
  component: MetricsRoute,
});

function MetricsRoute() {
  const search = Route.useSearch();
  return <ConsoleMetricsPage search={search} />;
}
