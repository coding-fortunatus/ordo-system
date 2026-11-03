import { createFileRoute } from "@tanstack/react-router";
import ExportsPage from "@/pages/exports/page";

export const Route = createFileRoute("/_app/exports")({
  head: () => ({ meta: [{ title: "Exports | Ordo" }] }),
  component: ExportsPage,
});
