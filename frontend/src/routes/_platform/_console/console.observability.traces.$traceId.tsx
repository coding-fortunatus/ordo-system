import { createFileRoute } from "@tanstack/react-router";
import NotFoundPage from "@/pages/not-found";
import ConsoleTraceDetailPage from "@/pages/platform/trace-detail";

export const Route = createFileRoute("/_platform/_console/console/observability/traces/$traceId")({
  head: () => ({ meta: [{ title: "Trace | Ordo console" }] }),
  component: TraceRoute,
});

function TraceRoute() {
  const { traceId } = Route.useParams();
  const id = traceId.toLowerCase();
  if (!/^[0-9a-f]{32}$/.test(id)) return <NotFoundPage />;
  return <ConsoleTraceDetailPage key={id} traceId={id} />;
}
