import { createFileRoute } from "@tanstack/react-router";
import CoursesListPage from "@/pages/courses/list";

export const Route = createFileRoute("/_app/courses")({
  head: () => ({ meta: [{ title: "Courses | Ordo" }] }),
  component: CoursesListPage,
});
