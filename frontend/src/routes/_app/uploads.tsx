import { createFileRoute } from "@tanstack/react-router";
import { RequireDataOfficer } from "@/components/route-guards";
import UploadsPage from "@/pages/uploads/page";

export const Route = createFileRoute("/_app/uploads")({
  head: () => ({ meta: [{ title: "Uploads | Ordo" }] }),
  component: () => (
    <RequireDataOfficer>
      <UploadsPage />
    </RequireDataOfficer>
  ),
});
