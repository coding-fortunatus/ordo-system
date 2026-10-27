import { useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { formatDistanceToNowStrict } from "date-fns";
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  EyeOff,
  RotateCcw,
  ScrollText,
  Waypoints,
  XCircle,
} from "lucide-react";
import {
  GROUP_STATUS_LABELS,
  HEALTH_LABELS,
  KIND_LABELS,
  SOURCE_LABELS,
  type ErrorContext,
  type ErrorEvent,
  type ErrorGroupStatus,
  type ErrorKind,
  type ErrorScope,
  type ErrorSource,
  type HealthCounts,
  type HealthStatus,
  type StackFrame,
  useErrorEvents,
  useErrorGroup,
  useErrorGroups,
  useScopeHealth,
  useSetErrorGroupStatus,
} from "@/api/observability";
import { TrendChart } from "@/components/trend-chart";
import { PaginationFooter } from "@/components/data-table/pagination";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
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
import { toast } from "@/lib/use-toast";

/** Status is recomputed every 5 minutes; older than this means it's stuck. */
const STALE_AFTER_MS = 15 * 60_000;
const PAGE_SIZE = 15;

export function timeAgo(iso: string | null): string {
  if (!iso) return "Never";
  return `${formatDistanceToNowStrict(new Date(iso))} ago`;
}

export function fullTime(iso: string | null): string | undefined {
  return iso ? new Date(iso).toLocaleString() : undefined;
}

export function isStale(computedAt: string | null): boolean {
  return !!computedAt && Date.now() - new Date(computedAt).getTime() > STALE_AFTER_MS;
}

const HEALTH_ICONS: Record<HealthStatus, typeof XCircle> = {
  failing: XCircle,
  degraded: AlertTriangle,
  healthy: CheckCircle2,
};

/** Told apart by label and icon, per the design system; failing is red. */
export function HealthBadge({ health }: { health: HealthStatus | null }) {
  if (!health) {
    return (
      <Badge variant="secondary">
        <CircleDashed aria-hidden />
        Not computed yet
      </Badge>
    );
  }
  const Icon = HEALTH_ICONS[health];
  const variant = health === "failing" ? "destructive" : health === "degraded" ? "warning" : "default";
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {HEALTH_LABELS[health]}
    </Badge>
  );
}

export const COUNT_LABELS: Array<[keyof HealthCounts, string]> = [
  ["server_errors", "Server errors"],
  ["client_errors", "4xx responses"],
  ["job_failures", "Failed jobs"],
  ["ws_errors", "WebSocket errors"],
  ["js_errors", "Browser errors"],
  ["requests", "Requests"],
];

function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">{children}</p>
  );
}

/**
 * Status, why, last hour counts and the 7 day hourly chart for one school or
 * Platform. Server errors are charted; the rest show in the tooltip.
 */
export function HealthCard({ scope }: { scope: ErrorScope }) {
  const health = useScopeHealth(scope);
  const data = health.data;

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3">
        <CardTitle>Health</CardTitle>
        {data && (
          <span
            className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground"
            title={fullTime(data.computed_at)}
          >
            {data.computed_at ? `Computed ${timeAgo(data.computed_at)}` : "Not computed yet"}
          </span>
        )}
      </CardHeader>
      <CardContent className="space-y-5 px-0">
        {health.isLoading ? (
          <div className="space-y-4 px-5">
            <Skeleton className="h-16" />
            <Skeleton className="h-[180px]" />
          </div>
        ) : health.error || !data ? (
          <Alert variant="destructive" className="mx-5 w-auto">
            <AlertDescription>{extractErrorEnvelope(health.error).detail}</AlertDescription>
          </Alert>
        ) : (
          <>
            <div className="flex flex-col gap-2 px-5 sm:flex-row sm:items-baseline sm:gap-4">
              <HealthBadge health={data.health} />
              <p className="text-[14px]">
                {data.health
                  ? data.reason
                  : "Status is worked out every 5 minutes once the health check is running."}
              </p>
            </div>
            {isStale(data.computed_at) && (
              <Alert className="mx-5 w-auto">
                <AlertTriangle aria-hidden />
                <AlertDescription>
                  This status is older than it should be. The health check runs every 5 minutes on
                  the worker, so a long job or a stopped worker is holding it up.
                </AlertDescription>
              </Alert>
            )}
            <dl className="grid grid-cols-2 gap-x-6 gap-y-4 px-5 sm:grid-cols-3 lg:grid-cols-6">
              {COUNT_LABELS.map(([field, label]) => (
                <div key={field}>
                  <dt>
                    <Eyebrow>{label}</Eyebrow>
                  </dt>
                  <dd
                    className={
                      field === "server_errors" && data[field] > 0
                        ? "mt-1 text-[22px] tabular-nums text-destructive"
                        : "mt-1 text-[22px] tabular-nums"
                    }
                  >
                    {data[field].toLocaleString()}
                  </dd>
                </div>
              ))}
              <p className="col-span-full -mt-2 text-[12px] text-muted-foreground">In the last hour.</p>
            </dl>
            <div>
              <div className="px-5">
                <Eyebrow>Server errors per hour, last 7 days</Eyebrow>
              </div>
              <TrendChart
                unit="hour"
                label="Server errors"
                empty="The chart fills in as the health check records each hour."
                points={data.buckets.map((b) => ({
                  date: b.hour,
                  value: b.server_errors,
                  details: [
                    ["4xx", b.client_errors],
                    ["Failed jobs", b.job_failures],
                    ["Expected job failures", b.expected_job_failures],
                    ["WebSocket", b.ws_errors],
                    ["Browser", b.js_errors],
                    ["Requests", b.requests],
                  ],
                }))}
              />
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

const ANY = "any";

function FilterSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T | undefined;
  options: Record<T, string>;
  onChange: (value: T | undefined) => void;
}) {
  return (
    <Select value={value ?? ANY} onValueChange={(v) => onChange(v === ANY ? undefined : (v as T))}>
      <SelectTrigger className="h-8 w-[150px] text-[12px]" aria-label={`Filter by ${label.toLowerCase()}`}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY}>Any {label.toLowerCase()}</SelectItem>
        {(Object.keys(options) as T[]).map((key) => (
          <SelectItem key={key} value={key}>
            {options[key]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** A school's (or Platform's) error groups, newest first, with filters. */
export function ErrorGroupsCard({ scope }: { scope: ErrorScope }) {
  const [status, setStatus] = useState<ErrorGroupStatus | undefined>("open");
  const [source, setSource] = useState<ErrorSource | undefined>();
  const [kind, setKind] = useState<ErrorKind | undefined>();
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState<number | null>(null);
  const groups = useErrorGroups(scope, { status, source, kind, page });
  const rows = groups.data?.results ?? [];
  const filtered = status !== undefined || source !== undefined || kind !== undefined;
  const filter =
    <T,>(set: (value: T) => void) =>
    (value: T) => {
      set(value);
      setPage(1);
    };

  return (
    <Card>
      <CardHeader className="gap-4 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle>Error groups</CardTitle>
        <div className="flex flex-wrap gap-2">
          <FilterSelect label="Status" value={status} options={GROUP_STATUS_LABELS} onChange={filter(setStatus)} />
          <FilterSelect label="Source" value={source} options={SOURCE_LABELS} onChange={filter(setSource)} />
          <FilterSelect label="Kind" value={kind} options={KIND_LABELS} onChange={filter(setKind)} />
        </div>
      </CardHeader>
      <CardContent className="px-0">
        {groups.isLoading ? (
          <div className="space-y-2 px-5">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : groups.error ? (
          <Alert variant="destructive" className="mx-5 w-auto">
            <AlertDescription>{extractErrorEnvelope(groups.error).detail}</AlertDescription>
          </Alert>
        ) : rows.length === 0 ? (
          <div className="grid place-items-center px-6 py-12 text-center">
            <Eyebrow>No errors</Eyebrow>
            <p className="mt-3 font-serif text-[1.15rem] italic text-muted-foreground">
              {filtered ? "Nothing matches these filters." : "Nothing has gone wrong here yet."}
            </p>
          </div>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Error</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead className="text-right">Count</TableHead>
                  <TableHead>Last seen</TableHead>
                  <TableHead>First seen</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((group) => (
                  <TableRow key={group.id}>
                    <TableCell className="max-w-[420px] whitespace-normal">
                      <button
                        type="button"
                        onClick={() => setOpenId(group.id)}
                        className="block text-left font-medium hover:underline focus-visible:underline focus-visible:outline-none"
                      >
                        <span className="line-clamp-2 break-words">{group.title}</span>
                      </button>
                      <span className="block truncate font-mono text-[11.5px] text-muted-foreground">
                        {group.culprit}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="block">{SOURCE_LABELS[group.source]}</span>
                      {group.kind === "expected" && (
                        <span className="block text-[12px] text-muted-foreground">Expected</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{group.count.toLocaleString()}</TableCell>
                    <TableCell className="font-mono text-[12px] text-muted-foreground" title={fullTime(group.last_seen)}>
                      {timeAgo(group.last_seen)}
                    </TableCell>
                    <TableCell className="font-mono text-[12px] text-muted-foreground" title={fullTime(group.first_seen)}>
                      {timeAgo(group.first_seen)}
                    </TableCell>
                    <TableCell>
                      <GroupStatusBadge status={group.status} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {groups.data && groups.data.count > PAGE_SIZE && (
              <div className="border-t border-[color:var(--border)] bg-[color:var(--muted)]/40 px-4 py-3">
                <PaginationFooter
                  page={page}
                  pageSize={PAGE_SIZE}
                  total={groups.data.count}
                  onPageChange={setPage}
                />
              </div>
            )}
          </>
        )}
      </CardContent>
      <ErrorGroupSheet id={openId} onClose={() => setOpenId(null)} />
    </Card>
  );
}

export function GroupStatusBadge({ status }: { status: ErrorGroupStatus }) {
  const variant = status === "open" ? "default" : "secondary";
  return <Badge variant={variant}>{GROUP_STATUS_LABELS[status]}</Badge>;
}

const ACTIONS: Array<{ to: ErrorGroupStatus; label: string; icon: typeof CheckCircle2; done: string }> = [
  { to: "resolved", label: "Resolve", icon: CheckCircle2, done: "Marked resolved. It reopens if it happens again." },
  { to: "ignored", label: "Ignore", icon: EyeOff, done: "Ignored. It keeps counting, but never alerts." },
  { to: "open", label: "Reopen", icon: RotateCcw, done: "Reopened." },
];

/** One group: what it is, its status actions, and its stored events. */
function ErrorGroupSheet({ id, onClose }: { id: number | null; onClose: () => void }) {
  const group = useErrorGroup(id);
  const [page, setPage] = useState(1);
  const events = useErrorEvents(id, page);
  const setStatus = useSetErrorGroupStatus();
  const data = group.data;

  const change = async (to: ErrorGroupStatus, done: string) => {
    if (id === null) return;
    try {
      await setStatus.mutateAsync({ id, status: to });
      toast({ title: done });
    } catch (err) {
      toast({
        title: "That didn't work",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <Sheet
      open={id !== null}
      onOpenChange={(open) => {
        if (!open) {
          onClose();
          setPage(1);
        }
      }}
    >
      <SheetContent className="w-full overflow-y-auto data-[side=right]:sm:max-w-2xl">
        {group.isLoading || !data ? (
          <div className="space-y-4 p-6">
            <SheetTitle className="sr-only">Loading error group</SheetTitle>
            <Skeleton className="h-8 w-3/4" />
            <Skeleton className="h-24" />
            <Skeleton className="h-48" />
          </div>
        ) : (
          <>
            <SheetHeader className="gap-3 border-b border-[color:var(--border)] p-6 pr-14">
              <div className="flex flex-wrap items-center gap-3">
                <GroupStatusBadge status={data.status} />
                <Badge variant="outline">{SOURCE_LABELS[data.source]}</Badge>
                {data.kind === "expected" && <Badge variant="outline">Expected</Badge>}
              </div>
              <SheetTitle className="break-words font-serif text-[1.5rem] font-normal leading-tight tracking-[-0.01em]">
                {data.title}
              </SheetTitle>
              <SheetDescription className="break-all font-mono text-[12px]">
                {data.exc_type} · {data.culprit}
              </SheetDescription>
              <div className="flex flex-wrap gap-2 pt-1">
                {ACTIONS.filter((a) => a.to !== data.status).map((action) => (
                  <Button
                    key={action.to}
                    size="sm"
                    variant="outline"
                    disabled={setStatus.isPending}
                    onClick={() => void change(action.to, action.done)}
                  >
                    <action.icon data-icon="inline-start" />
                    {action.label}
                  </Button>
                ))}
              </div>
            </SheetHeader>

            <div className="space-y-8 p-6">
              <dl className="grid grid-cols-2 gap-x-6 gap-y-4 text-[14px] sm:grid-cols-3">
                <Fact label="Occurrences">{data.count.toLocaleString()}</Fact>
                <Fact label="First seen" title={fullTime(data.first_seen)}>
                  {timeAgo(data.first_seen)}
                </Fact>
                <Fact label="Last seen" title={fullTime(data.last_seen)}>
                  {timeAgo(data.last_seen)}
                </Fact>
                <Fact label="Last release">
                  <span className="font-mono text-[12.5px]">{data.last_release || "Unknown"}</span>
                </Fact>
                {data.resolved_by && (
                  <Fact label="Status set by">{data.resolved_by}</Fact>
                )}
                {data.resolved_at && (
                  <Fact label="Resolved" title={fullTime(data.resolved_at)}>
                    {timeAgo(data.resolved_at)}
                  </Fact>
                )}
              </dl>

              <section aria-labelledby="error-events-heading" className="space-y-4">
                <div className="flex items-baseline justify-between gap-4">
                  <h3
                    id="error-events-heading"
                    className="text-xs font-semibold uppercase tracking-wider"
                  >
                    Stored occurrences
                  </h3>
                  <span className="text-[12px] text-muted-foreground">
                    The first 10 each hour, then every 100th
                  </span>
                </div>
                {events.isLoading ? (
                  <Skeleton className="h-48" />
                ) : events.error ? (
                  <Alert variant="destructive">
                    <AlertDescription>{extractErrorEnvelope(events.error).detail}</AlertDescription>
                  </Alert>
                ) : events.data?.results.length ? (
                  <>
                    <ol className="space-y-4">
                      {events.data.results.map((event) => (
                        <li key={event.id}>
                          <EventCard event={event} />
                        </li>
                      ))}
                    </ol>
                    {events.data.count > PAGE_SIZE && (
                      <PaginationFooter
                        page={page}
                        pageSize={PAGE_SIZE}
                        total={events.data.count}
                        onPageChange={setPage}
                      />
                    )}
                  </>
                ) : (
                  <p className="text-[13px] text-muted-foreground">
                    No occurrences are stored. Older ones are removed after 30 days.
                  </p>
                )}
              </section>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

function Fact({ label, title, children }: { label: string; title?: string; children: ReactNode }) {
  return (
    <div>
      <dt>
        <Eyebrow>{label}</Eyebrow>
      </dt>
      <dd className="mt-1" title={title}>
        {children}
      </dd>
    </div>
  );
}

const CONTEXT_LABELS: Array<[keyof ErrorContext, string]> = [
  ["method", "Method"],
  ["route", "Route"],
  ["status", "Status"],
  ["page_path", "Page"],
  ["task_name", "Task"],
  ["job_id", "Job"],
  ["user_id", "User id"],
  ["role", "Role"],
  ["support", "Ordo support"],
  ["request_id", "Request id"],
  ["user_agent", "Browser"],
];

function EventCard({ event }: { event: ErrorEvent }) {
  const context = CONTEXT_LABELS.filter(([key]) => event.context[key] !== undefined);
  return (
    <article className="border border-[color:var(--border)] bg-card">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-[color:var(--border)] px-4 py-3">
        <time
          dateTime={event.occurred_at}
          className="font-mono text-[11.5px] uppercase tracking-[0.12em] text-muted-foreground"
        >
          {new Date(event.occurred_at).toLocaleString()}
        </time>
        {event.release && (
          <span className="font-mono text-[11px] text-muted-foreground">Release {event.release}</span>
        )}
      </header>
      <div className="space-y-4 px-4 py-4">
        {event.message && <p className="whitespace-pre-wrap break-words text-[13.5px]">{event.message}</p>}
        {context.length > 0 && (
          <dl className="grid grid-cols-[110px_1fr] gap-x-4 gap-y-1.5 text-[12.5px]">
            {context.map(([key, label]) => (
              <div key={key} className="contents">
                <dt className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-muted-foreground pt-0.5">
                  {label}
                </dt>
                <dd className="min-w-0 break-all font-mono">
                  {key === "support" ? (event.context.support ? "Yes" : "No") : String(event.context[key])}
                </dd>
              </div>
            ))}
          </dl>
        )}
        <StackTrace frames={event.stack} />
        <TraceLinks event={event} />
      </div>
    </article>
  );
}

/** The request or job the error happened in, and its logs (spec 0006, AC-15). */
function TraceLinks({ event }: { event: ErrorEvent }) {
  if (!event.trace_id) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-[color:var(--border)] pt-3">
      {event.trace_kept ? (
        <Button
          variant="outline"
          size="sm"
          render={<Link to="/console/observability/traces/$traceId" params={{ traceId: event.trace_id }} />}
        >
          <Waypoints data-icon="inline-start" />
          View trace
        </Button>
      ) : (
        <span className="text-[12px] text-muted-foreground">Trace expired</span>
      )}
      {event.logs_kept ? (
        <Button
          variant="ghost"
          size="sm"
          render={<Link to="/console/observability/logs" search={{ trace: event.trace_id }} />}
        >
          <ScrollText data-icon="inline-start" />
          View logs
        </Button>
      ) : (
        <span className="text-[12px] text-muted-foreground">Logs expired</span>
      )}
    </div>
  );
}

/** Innermost call last, like a Python traceback. */
function StackTrace({ frames }: { frames: StackFrame[] }) {
  if (!frames.length) {
    return <p className="text-[12px] text-muted-foreground">No stack trace for this one.</p>;
  }
  return (
    <details open className="group">
      <summary className="cursor-pointer font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground">
        Stack trace, {frames.length} {frames.length === 1 ? "frame" : "frames"}, most recent call last
      </summary>
      <ol className="mt-2 max-h-[420px] overflow-auto bg-[color:var(--muted)]/50 py-2 font-mono text-[12px]">
        {frames.map((frame, i) => (
          <li key={i} className="px-3 py-1.5">
            <span className="break-all">
              {frame.file}
              <span className="text-muted-foreground">:{frame.line}</span>
            </span>{" "}
            <span className="text-muted-foreground">in</span> <span>{frame.function}</span>
            {frame.code && <code className="mt-0.5 block whitespace-pre-wrap pl-4 text-muted-foreground">{frame.code}</code>}
          </li>
        ))}
      </ol>
    </details>
  );
}
