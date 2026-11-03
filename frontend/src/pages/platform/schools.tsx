import { useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { Download, Loader2, MoreHorizontal, Plus } from "lucide-react";
import {
  SCHOOL_STATUS_LABELS,
  type SchoolRow,
  type SchoolStatus,
  type SchoolUsage,
  useReactivateSchool,
  useResendSchoolInvite,
  useRestoreSchool,
  useRetrySchool,
  useSchools,
} from "@/api/platform";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ListShell } from "@/components/data-table/list-shell";
import { PaginationFooter } from "@/components/data-table/pagination";
import { ACTIONS_HEAD_CLASS, RowActions } from "@/components/data-table/row-actions";
import { extractErrorEnvelope, platformApi } from "@/lib/api";
import { downloadAuthenticatedFile } from "@/lib/download";
import { useConfirm } from "@/lib/confirm";
import { isPlatformOwner, usePlatformAuth } from "@/lib/platform-auth";
import { toast } from "@/lib/use-toast";

const STATUS_TONES: Record<SchoolStatus, "default" | "secondary" | "destructive" | "warning"> = {
  provisioning: "warning",
  active: "default",
  failed: "destructive",
  suspended: "warning",
  pending_deletion: "destructive",
  purged: "secondary",
};

export function SchoolStatusBadge({ status }: { status: SchoolStatus }) {
  return (
    <Badge variant={STATUS_TONES[status]}>
      {status === "provisioning" && <Loader2 className="animate-spin" aria-hidden />}
      {SCHOOL_STATUS_LABELS[status]}
    </Badge>
  );
}

export function formatDay(iso: string | null): string {
  if (!iso) return "Not set";
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
  });
}

const ALL = "all";

/** Every school on the platform. */
export default function ConsoleSchoolsPage() {
  const { admin } = usePlatformAuth();
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<SchoolStatus | typeof ALL>(ALL);
  const [exporting, setExporting] = useState(false);
  const list = useSchools({
    page,
    q: query || undefined,
    status: status === ALL ? undefined : status,
  });
  const filtered = Boolean(query) || status !== ALL;

  return (
    <ListShell
      section="Console · Platform"
      title="Schools"
      description="Every school on Ordo. Creating one builds its own private database and emails its first administrator an invite."
      toolbar={
        <>
          <Button
            variant="outline"
            disabled={exporting}
            onClick={async () => {
              setExporting(true);
              try {
                await downloadAuthenticatedFile(
                  "/schools/export/",
                  "ordo-schools.csv",
                  { q: query || undefined, status: status === ALL ? undefined : status },
                  platformApi,
                );
              } catch (err) {
                toast({
                  title: "Export failed",
                  description: extractErrorEnvelope(err).detail,
                  variant: "destructive",
                });
              } finally {
                setExporting(false);
              }
            }}
          >
            <Download data-icon="inline-start" />
            Export CSV
          </Button>
          {isPlatformOwner(admin) && (
            <Link to="/console/schools/new" className={buttonVariants({ variant: "brand" })}>
              <Plus data-icon="inline-start" />
              New school
            </Link>
          )}
        </>
      }
      query={query}
      onQueryChange={(q) => {
        setQuery(q);
        setPage(1);
      }}
      searchPlaceholder="Search by name or address"
      filters={
        <Select
          value={status}
          onValueChange={(v) => {
            setStatus(v as SchoolStatus | typeof ALL);
            setPage(1);
          }}
        >
          <SelectTrigger className="h-8 w-[170px] text-[12px]" aria-label="Filter by status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Any status</SelectItem>
            {(Object.keys(SCHOOL_STATUS_LABELS) as SchoolStatus[]).map((s) => (
              <SelectItem key={s} value={s}>
                {SCHOOL_STATUS_LABELS[s]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      }
      isLoading={list.isLoading}
      error={list.error}
      isEmpty={!list.data?.results.length}
      emptyMessage={
        filtered
          ? "No schools match these filters."
          : isPlatformOwner(admin)
            ? "No schools yet. Create the first one with New school."
            : "No schools yet. An Owner creates them."
      }
      pagination={
        list.data && (
          <PaginationFooter page={page} pageSize={15} total={list.data.count} onPageChange={setPage} />
        )
      }
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>School</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Plan</TableHead>
            <TableHead>First admin</TableHead>
            <TableHead>Usage</TableHead>
            <TableHead>Last active</TableHead>
            <TableHead>Created</TableHead>
            <TableHead className={ACTIONS_HEAD_CLASS}>
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {list.data?.results.map((row) => (
            <TableRow key={row.id}>
              <TableCell>
                <Link
                  to="/console/schools/$id"
                  params={{ id: String(row.id) }}
                  className="block font-medium hover:underline"
                >
                  {row.name}
                </Link>
                <span className="block font-mono text-[11.5px] text-muted-foreground">
                  {row.domain ?? row.slug}
                </span>
              </TableCell>
              <TableCell>
                <SchoolStatusBadge status={row.status} />
                {row.status === "failed" && row.provisioning_error && (
                  <span
                    className="mt-1 block max-w-[240px] truncate font-mono text-[11px] text-muted-foreground"
                    title={row.provisioning_error}
                  >
                    {row.provisioning_error}
                  </span>
                )}
              </TableCell>
              <TableCell>{row.plan.name}</TableCell>
              <TableCell>
                <span className="block">{row.first_admin_name}</span>
                <span className="block text-[12px] text-muted-foreground">{row.first_admin_email}</span>
              </TableCell>
              <TableCell>
                <SchoolUsageCell usage={row.usage} />
              </TableCell>
              <TableCell className="whitespace-nowrap font-mono text-[12px] text-muted-foreground">
                {!row.usage
                  ? "No data yet"
                  : row.usage.last_activity_at
                    ? formatDay(row.usage.last_activity_at)
                    : "Never"}
              </TableCell>
              <TableCell className="whitespace-nowrap font-mono text-[12px] text-muted-foreground">
                {formatDay(row.created_at)}
              </TableCell>
              <TableCell>
                <RowActions>
                  <SchoolActions row={row} />
                </RowActions>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </ListShell>
  );
}

/** Counts from the latest nightly snapshot, so up to a day old. */
function SchoolUsageCell({ usage }: { usage: SchoolUsage | null }) {
  if (!usage) {
    return <span className="text-[12px] text-muted-foreground">No data yet</span>;
  }
  return (
    <span className="block whitespace-nowrap" title={`Snapshot of ${usage.taken_on} (UTC)`}>
      <span className="block">
        {usage.students.toLocaleString()} students · {usage.staff_users.toLocaleString()} staff
      </span>
      <span className="block text-[12px] text-muted-foreground">
        {usage.jobs_last_24h.toLocaleString()} {usage.jobs_last_24h === 1 ? "job" : "jobs"} in 24h
      </span>
    </span>
  );
}

function SchoolActions({ row }: { row: SchoolRow }) {
  const { admin } = usePlatformAuth();
  const actions = useSchoolActions();
  const navigate = useNavigate();
  const canRetry = isPlatformOwner(admin) && row.status === "failed";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="grid size-8 place-items-center rounded-md text-muted-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/40"
        aria-label={`Actions for ${row.name}`}
      >
        <MoreHorizontal className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[200px]">
        <DropdownMenuItem
          onClick={() =>
            navigate({ to: "/console/schools/$id", params: { id: String(row.id) } })
          }
        >
          Open
        </DropdownMenuItem>
        {canRetry && (
          <DropdownMenuItem onClick={() => void actions.retry(row)}>Retry setup</DropdownMenuItem>
        )}
        {row.status === "active" && row.first_admin_joined === false && (
          <DropdownMenuItem onClick={() => void actions.resend(row)}>
            Resend first admin invite
          </DropdownMenuItem>
        )}
        {row.status === "suspended" && (
          <DropdownMenuItem onClick={() => void actions.reactivate(row)}>Reactivate</DropdownMenuItem>
        )}
        {isPlatformOwner(admin) && row.status === "pending_deletion" && (
          <DropdownMenuItem onClick={() => void actions.restore(row)}>Restore</DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Retry, resend, reactivate and restore, with their confirmations and toasts,
 * for list and detail. Suspend and delete take typed input: see school-closure.
 */
export function useSchoolActions() {
  const confirm = useConfirm();
  const retry = useRetrySchool();
  const resend = useResendSchoolInvite();
  const reactivateSchool = useReactivateSchool();
  const restoreSchool = useRestoreSchool();

  const run = async (action: () => Promise<unknown>, done: string) => {
    try {
      await action();
      toast({ title: done });
    } catch (err) {
      toast({
        title: "That didn't work",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return {
    pending:
      retry.isPending || resend.isPending || reactivateSchool.isPending || restoreSchool.isPending,
    retry: async (school: Pick<SchoolRow, "id" | "name">) => {
      const ok = await confirm({
        title: `Retry setting up ${school.name}?`,
        description:
          "Whatever the failed attempt left behind is dropped, and setup starts again from an empty database.",
        confirmLabel: "Retry setup",
      });
      if (ok) await run(() => retry.mutateAsync(school.id), `Setting up ${school.name} again`);
    },
    resend: async (school: Pick<SchoolRow, "id" | "first_admin_email">) => {
      const ok = await confirm({
        title: "Resend the first admin invite?",
        description: `${school.first_admin_email} gets a new link that lasts 7 days. Any earlier link stops working.`,
        confirmLabel: "Resend invite",
      });
      if (ok) {
        await run(
          () => resend.mutateAsync(school.id),
          `New invite sent to ${school.first_admin_email}`,
        );
      }
    },
    reactivate: async (school: Pick<SchoolRow, "id" | "name">) => {
      const ok = await confirm({
        title: `Reactivate ${school.name}?`,
        description: "Its users can sign in and work again straight away.",
        confirmLabel: "Reactivate",
      });
      if (ok) await run(() => reactivateSchool.mutateAsync(school.id), `${school.name} is active again`);
    },
    restore: async (school: Pick<SchoolRow, "id" | "name">) => {
      const ok = await confirm({
        title: `Restore ${school.name}?`,
        description:
          "The deletion is cancelled and the final backup is removed. A school that never finished setting up goes back to Setup failed, so you can retry it.",
        confirmLabel: "Restore",
      });
      if (ok) await run(() => restoreSchool.mutateAsync(school.id), `${school.name} is restored`);
    },
  };
}
