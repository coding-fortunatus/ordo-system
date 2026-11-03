import { createFileRoute } from "@tanstack/react-router";
import { RequireSuperAdmin } from "@/components/route-guards";
import JobsListPage from "@/pages/jobs/list";

export const Route = createFileRoute("/_app/jobs")({
  head: () => ({ meta: [{ title: "Jobs | Ordo" }] }),
  component: () => (
    <RequireSuperAdmin>
      <JobsListPage />
    </RequireSuperAdmin>
  ),
});
