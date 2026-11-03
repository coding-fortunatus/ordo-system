import { createFileRoute } from "@tanstack/react-router";
import StudentsListPage from "@/pages/students/list";

export const Route = createFileRoute("/_app/students")({
  head: () => ({ meta: [{ title: "Students | Ordo" }] }),
  component: StudentsListPage,
});
