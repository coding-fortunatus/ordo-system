import { createFileRoute } from "@tanstack/react-router";
import type { SegmentKind, StatusClass, TraceFilters, TraceRange } from "@/api/telemetry";
import { str } from "@/lib/search";
import ConsoleTracesPage from "@/pages/platform/traces";

const KINDS = ["http", "job", "ws"];
const STATUSES = ["2xx", "4xx", "5xx"];
const RANGES = ["1h", "6h", "24h", "all"];

function oneOf<T extends string>(value: unknown, allowed: string[]): T | undefined {
  const text = str(value);
  return text && allowed.includes(text) ? (text as T) : undefined;
}

export const Route = createFileRoute("/_platform/_console/console/observability/traces/")({
  validateSearch: (search: Record<string, unknown>): TraceFilters => {
    const minMs = Number(str(search.min_ms));
    return {
      school: str(search.school),
      kind: oneOf<SegmentKind>(search.kind, KINDS),
      status: oneOf<StatusClass>(search.status, STATUSES),
      errors: search.errors === true || search.errors === "true" || undefined,
      min_ms: Number.isInteger(minMs) && minMs > 0 ? minMs : undefined,
      route: str(search.route),
      trace: str(search.trace),
      range: oneOf<TraceRange>(search.range, RANGES),
    };
  },
  head: () => ({ meta: [{ title: "Traces | Ordo console" }] }),
  component: TracesRoute,
});

function TracesRoute() {
  const filters = Route.useSearch();
  return <ConsoleTracesPage filters={filters} />;
}
