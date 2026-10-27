import { createFileRoute } from "@tanstack/react-router";
import ConsoleDashboardsPage from "@/pages/platform/dashboards";

export const Route = createFileRoute("/_platform/_console/console/observability/dashboards/")({
  head: () => ({ meta: [{ title: "Dashboards | Ordo console" }] }),
  component: ConsoleDashboardsPage,
});
