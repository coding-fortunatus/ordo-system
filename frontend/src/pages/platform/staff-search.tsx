import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { RefreshCw, Search } from "lucide-react";
import { useReindexStaff, useStaffSearch } from "@/api/platform";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { extractErrorEnvelope } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/auth";
import { toast } from "@/lib/use-toast";
import { SchoolStatusBadge, formatDay } from "./schools";

/** Which school a staff email belongs to, from the nightly index. */
export default function ConsoleStaffSearchPage() {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const search = useStaffSearch(query);
  const reindex = useReindexStaff();

  // Wait for a pause in typing before searching.
  useEffect(() => {
    const t = setTimeout(() => setQuery(input), 300);
    return () => clearTimeout(t);
  }, [input]);

  const results = search.data?.results ?? [];

  return (
    <div className="space-y-8">
      <PageHeader
        section="Console · Platform"
        title="Find staff"
        description="Look up which school a staff email or name belongs to. Students aren't included. The index is rebuilt every night."
        actions={
          <Button
            variant="outline"
            disabled={reindex.isPending}
            onClick={async () => {
              await reindex.mutateAsync();
              toast({ title: "Rebuilding the index", description: "Search again in a minute." });
            }}
          >
            <RefreshCw data-icon="inline-start" />
            Rebuild now
          </Button>
        }
      />

      <div className="relative max-w-md">
        <Search className="absolute top-1/2 left-0 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          autoFocus
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Email or name, at least 3 characters"
          className="pl-6"
          aria-label="Search staff"
        />
      </div>

      {search.error && (
        <Alert variant="destructive">
          <AlertDescription>{extractErrorEnvelope(search.error).detail}</AlertDescription>
        </Alert>
      )}

      {query.trim().length >= 3 && (
        <div className="border border-[color:var(--border)] bg-card">
          {search.isLoading ? (
            <div className="space-y-2 p-5">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : results.length === 0 ? (
            <p className="px-5 py-8 text-center text-[13px] text-muted-foreground">
              No staff match “{query.trim()}”.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Staff</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>School</TableHead>
                  <TableHead>Last sign in</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {results.map((hit) => (
                  <TableRow key={`${hit.school.id}-${hit.email}`}>
                    <TableCell>
                      <span className="block font-medium">{hit.name || hit.email}</span>
                      {hit.name && (
                        <span className="block font-mono text-[11px] text-muted-foreground">{hit.email}</span>
                      )}
                      {!hit.is_active && <Badge variant="secondary">Deactivated</Badge>}
                    </TableCell>
                    <TableCell className="text-[13px]">
                      {ROLE_LABELS[hit.role as keyof typeof ROLE_LABELS] ?? "Department officer"}
                    </TableCell>
                    <TableCell>
                      <Link
                        to="/console/schools/$id"
                        params={{ id: String(hit.school.id) }}
                        className="font-medium hover:underline"
                      >
                        {hit.school.name}
                      </Link>
                      <div className="mt-1">
                        <SchoolStatusBadge status={hit.school.status} />
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-[12px] text-muted-foreground">
                      {hit.last_login ? formatDay(hit.last_login) : "Never"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      )}

      {search.data?.indexed_at && (
        <p className="font-mono text-[11px] text-muted-foreground">
          Index built {new Date(search.data.indexed_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}
