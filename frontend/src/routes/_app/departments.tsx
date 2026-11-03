import { createFileRoute } from "@tanstack/react-router";
import { RequireDataOfficer } from "@/components/route-guards";
import DepartmentsListPage from "@/pages/departments/list";

export const Route = createFileRoute("/_app/departments")({
  head: () => ({ meta: [{ title: "Departments | Ordo" }] }),
  component: () => (
    <RequireDataOfficer>
      <DepartmentsListPage />
    </RequireDataOfficer>
  ),
});
