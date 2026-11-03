import { createFileRoute } from "@tanstack/react-router";
import ConsoleDemoRequestsPage from "@/pages/platform/demo-requests";

export const Route = createFileRoute("/_platform/_console/console/requests")({
  head: () => ({ meta: [{ title: "Requests | Ordo console" }] }),
  // ?id= opens one request; the alert emails link straight to it.
  validateSearch: (search: Record<string, unknown>): { id?: number } => {
    const id = Number(search.id);
    return Number.isInteger(id) && id > 0 ? { id } : {};
  },
  component: ConsoleDemoRequestsPage,
});
