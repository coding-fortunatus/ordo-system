import { createFileRoute } from "@tanstack/react-router";
import ClassDetailPage from "@/pages/classes/detail";

export const Route = createFileRoute("/_app/classes/$id")({
  head: () => ({ meta: [{ title: "Class | Ordo" }] }),
  component: ClassDetailPage,
});
