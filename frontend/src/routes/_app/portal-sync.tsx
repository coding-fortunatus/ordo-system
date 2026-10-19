import { createFileRoute } from "@tanstack/react-router";
import { RequireDataOfficer, RequireFeature } from "@/components/route-guards";
import PortalSyncPage from "@/pages/portal-sync/page";

export const Route = createFileRoute("/_app/portal-sync")({
  component: () => (
    <RequireDataOfficer>
      <RequireFeature feature="portal_sync">
        <PortalSyncPage />
      </RequireFeature>
    </RequireDataOfficer>
  ),
});
