import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { AlertTriangle } from "lucide-react";
import { HEALTH_LABELS, type HealthRow, type HealthStatus, useHealthList } from "@/api/observability";
import { PageHeader } from "@/components/layout/page-header";
import { PaginationFooter } from "@/components/data-table/pagination";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { extractErrorEnvelope } from "@/lib/api";
import { ErrorGroupsCard, HealthBadge, HealthCard, fullTime, isStale, timeAgo } from "./errors";

const PAGE_SIZE = 15;

/**
 * Every live school plus Platform, worst first, refreshed every minute
 * (spec 0004, AC-14). Platform's own errors are listed below the table,
 * since Platform has no school page.
 */
export default function ConsoleHealthPage() {
  const [page, setPage] = useState(1);
  const list = useHealthList(page);
  const rows = list.data?.results ?? [];
  const newest = rows
    .map((r) => r.computed_at)
    .filter((t): t is string => !!t)
    .sort()
    .at(-1) ?? null;

  return (
    <div className="space-y-10">
      <PageHeader
        section="Console · Platform"
        title="School health"
        description="Server errors, failed jobs, refused sockets and browser errors in each school over the last hour, worst first. This page refreshes every minute."
        meta={
          newest && (
            <span
              className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground"
              title={fullTime(newest)}
            >
              Last computed {timeAgo(newest)}
            </span>
          )
        }
      />

      {isStale(newest) && (
        <Alert>
          <AlertTriangle aria-hidden />
          <AlertTitle>Status may be out of date</AlertTitle>
          <AlertDescription>
            The health check runs every 5 minutes on the worker. If this stays, check that the
            worker and beat are running, and that Redis is reachable.
          </AlertDescription>
        </Alert>
      )}

      <Summary rows={rows} total={list.data?.count} loading={list.isLoading} />

      <div className="overflow-hidden border border-[color:var(--border)] bg-card">
        {list.error ? (
          <Alert variant="destructive" className="m-4 w-auto">
            <AlertDescription>{extractErrorEnvelope(list.error).detail}</AlertDescription>
          </Alert>
        ) : list.isLoading ? (
          <div className="space-y-2 p-6">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>School</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Why</TableHead>
                <TableHead className="text-right">Server errors</TableHead>
                <TableHead className="text-right">4xx</TableHead>
                <TableHead className="text-right">Failed jobs</TableHead>
                <TableHead className="text-right">WebSocket</TableHead>
                <TableHead className="text-right">Browser</TableHead>
                <TableHead className="text-right">Requests</TableHead>
                <TableHead className="text-right">Open groups</TableHead>
                <TableHead>Computed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <HealthTableRow key={row.school?.id ?? "platform"} row={row} />
              ))}
            </TableBody>
          </Table>
        )}
        {list.data && list.data.count > PAGE_SIZE && (
          <div className="border-t border-[color:var(--border)] bg-[color:var(--muted)]/40 px-4 py-3">
            <PaginationFooter page={page} pageSize={PAGE_SIZE} total={list.data.count} onPageChange={setPage} />
          </div>
        )}
      </div>

      <section id="platform" aria-labelledby="platform-heading" className="scroll-mt-24 space-y-6">
        <div>
          <h2 id="platform-heading" className="font-serif text-[1.75rem] leading-tight tracking-[-0.01em]">
            Platform
          </h2>
          <p className="mt-2 max-w-2xl text-[14px] leading-[1.65] text-muted-foreground">
            Errors outside any school: this console, nightly tasks, email, backups, and requests to
            an address that isn't a school.
          </p>
        </div>
        <HealthCard scope="platform" />
        <ErrorGroupsCard scope="platform" />
      </section>
    </div>
  );
}

function HealthTableRow({ row }: { row: HealthRow }) {
  const quiet = "text-right tabular-nums text-muted-foreground";
  const count = (n: number, loud = false) => (
    <TableCell className={n > 0 && loud ? "text-right tabular-nums text-destructive" : n > 0 ? "text-right tabular-nums" : quiet}>
      {n.toLocaleString()}
    </TableCell>
  );
  return (
    <TableRow>
      <TableCell>
        {row.school ? (
          <>
            <Link
              to="/console/schools/$id"
              params={{ id: String(row.school.id) }}
              hash="errors"
              className="block font-medium hover:underline"
            >
              {row.school.name}
            </Link>
            <span className="block font-mono text-[11.5px] text-muted-foreground">{row.school.slug}</span>
          </>
        ) : (
          <>
            <a href="#platform" className="block font-medium hover:underline">
              Platform
            </a>
            <span className="block font-mono text-[11.5px] text-muted-foreground">console and tasks</span>
          </>
        )}
      </TableCell>
      <TableCell>
        <HealthBadge health={row.health} />
      </TableCell>
      <TableCell className="max-w-[320px] whitespace-normal text-[13px]">
        {row.health ? row.reason : <span className="text-muted-foreground">Waiting for the first check</span>}
      </TableCell>
      {count(row.server_errors, true)}
      {count(row.client_errors)}
      {count(row.job_failures)}
      {count(row.ws_errors)}
      {count(row.js_errors)}
      {count(row.requests)}
      {count(row.open_groups)}
      <TableCell className="whitespace-nowrap font-mono text-[12px] text-muted-foreground" title={fullTime(row.computed_at)}>
        {row.computed_at ? timeAgo(row.computed_at) : "Never"}
      </TableCell>
    </TableRow>
  );
}

const ORDER: Array<HealthStatus | null> = ["failing", "degraded", "healthy", null];

/** How many schools sit in each status, on this page of the list. */
function Summary({ rows, total, loading }: { rows: HealthRow[]; total?: number; loading: boolean }) {
  if (loading) return <Skeleton className="h-20" />;
  const tally = (status: HealthStatus | null) => rows.filter((r) => r.health === status).length;
  const partial = total !== undefined && total > rows.length;
  return (
    <div>
      <dl className="grid grid-cols-2 border border-[color:var(--border)] bg-card sm:grid-cols-4">
        {ORDER.map((status, i) => {
          const n = tally(status);
          return (
            <div
              key={status ?? "unknown"}
              className={`px-5 py-4 ${i > 0 ? "border-l border-[color:var(--border)]" : ""} ${i === 2 ? "max-sm:border-l-0 max-sm:border-t" : ""} ${i === 3 ? "max-sm:border-t" : ""}`}
            >
              <dt className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">
                {status ? HEALTH_LABELS[status] : "Not computed"}
              </dt>
              <dd
                className={
                  status === "failing" && n > 0
                    ? "mt-1 font-serif text-[2rem] leading-none text-destructive"
                    : "mt-1 font-serif text-[2rem] leading-none"
                }
              >
                {n}
              </dd>
            </div>
          );
        })}
      </dl>
      {partial && (
        <p className="mt-2 text-[12px] text-muted-foreground">Counts cover the schools on this page.</p>
      )}
    </div>
  );
}
