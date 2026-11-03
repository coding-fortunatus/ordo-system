import { createFileRoute } from "@tanstack/react-router";
import TimetablePage from "@/pages/timetable/page";

export const Route = createFileRoute("/_app/timetable")({
  head: () => ({ meta: [{ title: "Timetable | Ordo" }] }),
  component: TimetablePage,
});
