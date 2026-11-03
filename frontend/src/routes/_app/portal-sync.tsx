import { createFileRoute } from "@tanstack/react-router";
import { RequireDataOfficer, RequireFeature } from "@/components/route-guards";
import PortalSyncPage from "@/pages/portal-sync/page";

export const Route = createFileRoute("/_app/portal-sync")({
  head: () => ({ meta: [{ title: "Portal sync | Ordo" }] }),
  component: () => (
    <RequireDataOfficer>
      <RequireFeature feature="portal_sync">
        <PortalSyncPage />
      </RequireFeature>
    </RequireDataOfficer>
  ),
});
