import { createFileRoute } from "@tanstack/react-router";
import { RequireCommittee } from "@/components/route-guards";
import DirectoryPage from "@/pages/directory/page";

export const Route = createFileRoute("/_app/directory")({
  head: () => ({ meta: [{ title: "Directory & VISA | Ordo" }] }),
  component: () => (
    <RequireCommittee>
      <DirectoryPage />
    </RequireCommittee>
  ),
});
