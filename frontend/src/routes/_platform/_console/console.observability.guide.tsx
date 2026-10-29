import { createFileRoute } from "@tanstack/react-router";
import ConsoleObservabilityGuidePage from "@/pages/platform/observability-guide";

export const Route = createFileRoute("/_platform/_console/console/observability/guide")({
  head: () => ({ meta: [{ title: "Observability guide | Ordo console" }] }),
  component: ConsoleObservabilityGuidePage,
});
