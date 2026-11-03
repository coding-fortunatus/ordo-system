import { useSearch } from "@tanstack/react-router";
import { useDemoRequest } from "@/api/platform";
import { Skeleton } from "@/components/ui/skeleton";
import { SchoolForm } from "./school-form";

export default function ConsoleNewSchoolPage() {
  const { request } = useSearch({ from: "/_platform/_console/console/schools/new" });
  const demo = useDemoRequest(request);
  if (request !== undefined && demo.isLoading) return <Skeleton className="h-96" />;
  if (!demo.data) return <SchoolForm />;
  // Prefilled from the website request: the requester becomes the first admin.
  return (
    <SchoolForm
      fromRequest={demo.data.id}
      prefill={{
        name: demo.data.institution,
        first_admin_name: demo.data.name,
        first_admin_email: demo.data.email,
        contact_name: demo.data.name,
        contact_email: demo.data.email,
      }}
    />
  );
}
