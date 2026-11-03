import { createFileRoute } from "@tanstack/react-router";
import ClassesListPage from "@/pages/classes/list";

export const Route = createFileRoute("/_app/classes/")({
  head: () => ({ meta: [{ title: "Classes | Ordo" }] }),
  component: ClassesListPage,
});
