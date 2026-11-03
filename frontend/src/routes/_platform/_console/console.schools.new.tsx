import { createFileRoute } from "@tanstack/react-router";
import { RequirePlatformOwner } from "@/components/route-guards";
import ConsoleNewSchoolPage from "@/pages/platform/school-new";

export const Route = createFileRoute("/_platform/_console/console/schools/new")({
  head: () => ({ meta: [{ title: "New school | Ordo console" }] }),
  // ?request= prefills the form from a website demo request and links the two.
  validateSearch: (search: Record<string, unknown>): { request?: number } => {
    const request = Number(search.request);
    return Number.isInteger(request) && request > 0 ? { request } : {};
  },
  component: () => (
    <RequirePlatformOwner>
      <ConsoleNewSchoolPage />
    </RequirePlatformOwner>
  ),
});
