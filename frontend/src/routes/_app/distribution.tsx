import { createFileRoute } from "@tanstack/react-router";
import { RequireCommittee, RequireFeature } from "@/components/route-guards";
import DistributionPage from "@/pages/distribution/page";

export const Route = createFileRoute("/_app/distribution")({
  head: () => ({ meta: [{ title: "Distribution | Ordo" }] }),
  component: () => (
    <RequireCommittee>
      <RequireFeature feature="distribution">
        <DistributionPage />
      </RequireFeature>
    </RequireCommittee>
  ),
});
