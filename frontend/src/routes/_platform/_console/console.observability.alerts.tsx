import { createFileRoute } from "@tanstack/react-router";
import ConsoleAlertsPage from "@/pages/platform/alerts";

export const Route = createFileRoute("/_platform/_console/console/observability/alerts")({
  head: () => ({ meta: [{ title: "Alerts | Ordo console" }] }),
  component: ConsoleAlertsPage,
});
