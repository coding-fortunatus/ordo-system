import { createFileRoute } from "@tanstack/react-router";
import type { LogFilters, LogLevel, TraceRange } from "@/api/telemetry";
import { str } from "@/lib/search";
import ConsoleLogsPage from "@/pages/platform/logs";

const LEVELS = ["info", "warning", "error", "critical"];
const RANGES = ["1h", "6h", "24h", "all"];
const ID = /^[0-9a-f]{32}$/;

function oneOf<T extends string>(value: unknown, allowed: string[]): T | undefined {
  const text = str(value);
  return text && allowed.includes(text) ? (text as T) : undefined;
}

function id(value: unknown): string | undefined {
  const text = str(value)?.toLowerCase();
  return text && ID.test(text) ? text : undefined;
}

export const Route = createFileRoute("/_platform/_console/console/observability/logs")({
  validateSearch: (search: Record<string, unknown>): LogFilters => {
    const user = Number(str(search.user));
    return {
      school: str(search.school),
      level: oneOf<LogLevel>(search.level, LEVELS),
      logger: str(search.logger),
      trace: id(search.trace),
      request: id(search.request),
      user: Number.isInteger(user) && user > 0 ? user : undefined,
      q: str(search.q),
      range: oneOf<TraceRange>(search.range, RANGES),
    };
  },
  head: () => ({ meta: [{ title: "Logs | Ordo console" }] }),
  component: LogsRoute,
});

function LogsRoute() {
  const filters = Route.useSearch();
  return <ConsoleLogsPage filters={filters} />;
}
