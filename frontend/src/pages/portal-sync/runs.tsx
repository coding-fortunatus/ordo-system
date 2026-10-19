import { useState } from "react";
import { Eye, History, ShieldAlert, Unplug } from "lucide-react";
import {
  type CountBucket,
  type DataType,
  type IssueKind,
  type RunStatus,
  type StartedRun,
  type SyncIssue,
  type SyncRun,
  useApproveRun,
  useConfirmMassDelete,
  useResolveIssue,
  useSyncIssues,
  useSyncRun,
  useSyncRuns,
} from "@/api/portal-sync";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { PaginationFooter } from "@/components/data-table/pagination";
import { extractErrorEnvelope } from "@/lib/api";
import { useConfirm } from "@/lib/confirm";
import { toast } from "@/lib/use-toast";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 15;

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  running: "Running",
  awaiting_approval: "Awaiting approval",
  approved: "Approved",
  superseded: "Superseded",
  succeeded: "Succeeded",
  partial: "Partial",
  held: "Held",
  failed: "Failed",
};

const TRIGGER_LABELS: Record<SyncRun["trigger"], string> = {
  manual: "Sync now",
  approval: "Approval",
  confirm: "Confirmed delete",
};

const DATA_TYPE_LABELS: Record<DataType, string> = {
  faculties: "Faculties",
  departments: "Departments",
  classes: "Classes",
  courses: "Courses",
  students: "Students",
};

const ISSUE_LABELS: Record<IssueKind, string> = {
  bad_row: "Bad row",
  delete_held_referenced: "Delete held: in use",
  mass_delete_held: "Mass delete held",
  plan_limit: "Plan limit",
  fetch_error: "Fetch error",
};

// The buckets a run's detail shows, in order (AC-4).
const BUCKETS = [
  ["created", "Created"],
  ["linked", "Linked"],
  ["updated", "Updated"],
  ["deleted", "Deleted"],
  ["skipped", "Skipped"],
  ["held", "Held"],
] as const;

const BUCKET_LABELS: Record<CountBucket, string> = {
  created: "Created",
  linked: "Linked",
  updated: "Updated",
  deleted: "Deleted",
  skipped: "Skipped",
  held: "Held",
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const variant =
    status === "failed"
      ? "destructive"
      : status === "held" || status === "partial"
        ? "warning"
        : status === "running"
          ? "secondary"
          : "default";
  return (
    <Badge variant={variant}>
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full bg-current",
          status === "running" && "animate-pulse",
        )}
      />
      {RUN_STATUS_LABELS[status]}
    </Badge>
  );
}

function when(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function totals(run: SyncRun) {
  const t = { created: 0, linked: 0, updated: 0, deleted: 0, skipped: 0, held: 0 };
  for (const c of Object.values(run.counts)) {
    if (!c) continue;
    for (const [k] of BUCKETS) t[k] += c[k];
  }
  return t;
}

interface ListProps {
  selected: number | null;
  onSelect: (id: number) => void;
}

/** Every sync, newest first. Choosing one opens its detail below. */
export function RunHistory({ selected, onSelect }: ListProps) {
  const [page, setPage] = useState(1);
  const runs = useSyncRuns(page);

  return (
    <Card>
      <CardHeader>
        <CardTitle>History</CardTitle>
        <CardDescription>Every sync, newest first. Choose one to see what it did.</CardDescription>
      </CardHeader>
      <CardContent>
        {runs.error ? (
          <Alert variant="destructive">
            <AlertDescription>{extractErrorEnvelope(runs.error).detail}</AlertDescription>
          </Alert>
        ) : runs.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : !runs.data?.results.length ? (
          <div className="flex flex-col items-center gap-3 rounded-[12px] border border-dashed border-[color:var(--border)] px-6 py-10 text-center">
            <History className="size-6 text-muted-foreground" strokeWidth={1.75} aria-hidden />
            <p className="text-[13.5px] text-muted-foreground">
              No syncs yet. Once the endpoint is mapped, press{" "}
              <strong className="font-semibold text-foreground">Sync now</strong>.
            </p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Started</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Trigger</TableHead>
                    <TableHead className="text-right">Created</TableHead>
                    <TableHead className="text-right">Updated</TableHead>
                    <TableHead className="text-right">Issues</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.data.results.map((run) => {
                    const t = totals(run);
                    const active = run.id === selected;
                    return (
                      <TableRow
                        key={run.id}
                        data-state={active ? "selected" : undefined}
                        className="cursor-pointer"
                        onClick={() => onSelect(run.id)}
                      >
                        <TableCell className="whitespace-nowrap">
                          <button
                            type="button"
                            className="text-left underline-offset-4 hover:underline focus-visible:underline focus-visible:outline-none"
                            aria-current={active ? "true" : undefined}
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelect(run.id);
                            }}
                          >
                            {when(run.started_at)}
                          </button>
                        </TableCell>
                        <TableCell>
                          <RunStatusBadge status={run.status} />
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {TRIGGER_LABELS[run.trigger]}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{t.created}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {t.updated + t.linked}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{run.issue_count}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
            {runs.data.count > PAGE_SIZE && (
              <PaginationFooter
                page={page}
                pageSize={PAGE_SIZE}
                total={runs.data.count}
                onPageChange={setPage}
              />
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

interface DetailProps {
  runId: number;
  /** The current connection; a run of another one is history only. */
  connectionId: number | null;
  locked: boolean;
  /** A real sync started from this preview's approval. */
  onStarted: (run: StartedRun) => void;
}

/** One run: counts per data type, its error, and its issues. A preview
 *  also lists example references and can be approved. */
export function RunDetail({ runId, connectionId, locked, onStarted }: DetailProps) {
  const run = useSyncRun(runId);
  const approve = useApproveRun();
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState<IssueKind | "__all__">("__all__");
  const issues = useSyncIssues(runId, page, kind === "__all__" ? undefined : kind);

  if (run.isLoading) return <Skeleton className="h-64 w-full" />;
  if (run.error || !run.data) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{extractErrorEnvelope(run.error).detail}</AlertDescription>
      </Alert>
    );
  }
  const r = run.data;
  const detached = r.connection === null || r.connection !== connectionId;
  const onApprove = async () => {
    try {
      onStarted(await approve.mutateAsync(r.id));
    } catch (err) {
      toast({
        title: "Could not approve the preview",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };
  // Apply order: the stored counts don't keep their key order.
  const types = (Object.keys(DATA_TYPE_LABELS) as DataType[]).flatMap((t) => {
    const c = r.counts[t];
    return c ? [[t, c] as const] : [];
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-3">
          Sync of {when(r.started_at)}
          <RunStatusBadge status={r.status} />
        </CardTitle>
        <CardDescription>
          {TRIGGER_LABELS[r.trigger]}
          {r.started_by_email ? ` by ${r.started_by_email}` : ""}
          {r.finished_at ? ` · finished ${when(r.finished_at)}` : " · still running"}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-8">
        {r.status === "awaiting_approval" && (
          <Alert className="border-[color:var(--accent-iris-fg)]/20 bg-[color:var(--accent-iris)]/40">
            <Eye />
            <AlertTitle className="font-serif text-[1.125rem]">
              Preview: nothing was applied
            </AlertTitle>
            <AlertDescription className="space-y-4">
              <p>
                This is what the first sync would do with your current setup. Check the
                counts, examples and issues below. If they look right, approve it and Ordo
                runs the sync for real. Changing the setup makes the next sync a preview
                again.
              </p>
              <Button
                onClick={onApprove}
                disabled={locked || approve.isPending}
                title={locked ? "Locked while a timetable exists" : undefined}
              >
                {approve.isPending ? "Starting…" : "Approve and sync"}
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {detached && (
          <Alert>
            <Unplug />
            <AlertTitle>From an earlier connection</AlertTitle>
            <AlertDescription>
              The portal was disconnected since, so this run is kept as history only. What it
              synced is Ordo's own now; edit or delete it from its own page.
            </AlertDescription>
          </Alert>
        )}

        {!detached && r.is_preview && r.status === "superseded" && (
          <Alert>
            <AlertTitle>This preview is out of date</AlertTitle>
            <AlertDescription>
              The setup changed, or a newer preview was made, so it can't be approved. Press
              Sync now for a fresh preview.
            </AlertDescription>
          </Alert>
        )}

        {r.status === "approved" && (
          <Alert>
            <AlertTitle>Approved</AlertTitle>
            <AlertDescription>
              Nothing was applied by this preview itself. The sync it started is listed in the
              history above.
            </AlertDescription>
          </Alert>
        )}

        {r.status === "failed" && r.error && (
          <Alert variant="destructive">
            <AlertTitle>Nothing was applied</AlertTitle>
            <AlertDescription>{r.error}</AlertDescription>
          </Alert>
        )}

        {!detached && r.status !== "running" && (
          <MassDeleteHeld run={r} locked={locked} onStarted={onStarted} />
        )}

        {types.map(([type, c]) => (
          <section key={type} aria-label={DATA_TYPE_LABELS[type]} className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h3 className="font-mono text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                {DATA_TYPE_LABELS[type]}
              </h3>
              <Badge variant={c.type_status === "held" ? "warning" : "secondary"}>
                {r.is_preview
                  ? c.type_status === "held"
                    ? "Would be held"
                    : "Would apply"
                  : c.type_status === "held"
                    ? "Held, nothing applied"
                    : "Applied"}
              </Badge>
            </div>
            <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[12px] border border-[color:var(--border)] bg-[color:var(--border)] sm:grid-cols-3 lg:grid-cols-6">
              {BUCKETS.map(([k, label]) => (
                <div key={k} className="bg-card p-5">
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                    {label}
                  </p>
                  <p className="mt-2 font-serif text-[1.75rem] tabular-nums leading-none">
                    {c[k].toLocaleString()}
                  </p>
                </div>
              ))}
            </div>
            <Examples examples={c.examples} />
          </section>
        ))}

        <section aria-labelledby="run-issues" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3
              id="run-issues"
              className="font-mono text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground"
            >
              Issues
            </h3>
            <Select
              value={kind}
              onValueChange={(v) => {
                setKind(v as IssueKind | "__all__");
                setPage(1);
              }}
            >
              <SelectTrigger className="w-[200px]" aria-label="Filter issues by kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All issues</SelectItem>
                {(Object.keys(ISSUE_LABELS) as IssueKind[]).map((k) => (
                  <SelectItem key={k} value={k}>
                    {ISSUE_LABELS[k]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {issues.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : issues.error ? (
            <Alert variant="destructive">
              <AlertDescription>{extractErrorEnvelope(issues.error).detail}</AlertDescription>
            </Alert>
          ) : !issues.data?.results.length ? (
            <p className="rounded-[12px] border border-dashed border-[color:var(--border)] px-5 py-6 text-[13.5px] text-muted-foreground">
              {kind === "__all__"
                ? "No issues. Every row was usable."
                : "No issues of this kind."}
            </p>
          ) : (
            <>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Kind</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Reference</TableHead>
                      <TableHead>What happened</TableHead>
                      <TableHead className="text-right">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {issues.data.results.map((i) => (
                      <TableRow key={i.id}>
                        <TableCell className="whitespace-nowrap">
                          <Badge variant={i.kind === "fetch_error" ? "destructive" : "warning"}>
                            {ISSUE_LABELS[i.kind]}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {DATA_TYPE_LABELS[i.data_type]}
                        </TableCell>
                        <TableCell className="font-mono text-[12.5px]">{i.row_ref || "—"}</TableCell>
                        <TableCell className="min-w-[240px]">{i.message}</TableCell>
                        <TableCell className="text-right">
                          <IssueAction issue={i} run={r} locked={locked} detached={detached} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {issues.data.count > PAGE_SIZE && (
                <PaginationFooter
                  page={page}
                  pageSize={PAGE_SIZE}
                  total={issues.data.count}
                  onPageChange={setPage}
                />
              )}
            </>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

/** A preview's sample references per bucket, folded away by default. */
function Examples({ examples }: { examples?: Partial<Record<CountBucket, string[]>> }) {
  const groups = (Object.keys(BUCKET_LABELS) as CountBucket[]).flatMap((b) => {
    const refs = examples?.[b];
    return refs?.length ? [[b, refs] as const] : [];
  });
  if (!groups.length) return null;
  return (
    <details className="rounded-[12px] border border-[color:var(--border)] px-4 py-3">
      <summary className="cursor-pointer text-[13px] font-medium">Examples</summary>
      <dl className="mt-3 space-y-3">
        {groups.map(([b, refs]) => (
          <div key={b}>
            <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
              {BUCKET_LABELS[b]} · first {refs.length}
            </dt>
            <dd className="mt-1 break-words font-mono text-[12.5px] leading-[1.7]">
              {refs.join(", ")}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

/** A run's mass delete holds (AC-11): the admin picks the types whose
 *  deletes are right and starts a sync allowed to make them. */
function MassDeleteHeld({
  run,
  locked,
  onStarted,
}: {
  run: SyncRun;
  locked: boolean;
  onStarted: (run: StartedRun) => void;
}) {
  const held = useSyncIssues(run.id, 1, "mass_delete_held");
  const confirmDeletes = useConfirmMassDelete();
  const [picked, setPicked] = useState<Set<DataType>>(new Set());
  const issues = held.data?.results ?? [];
  if (!issues.length) return null;

  const toggle = (t: DataType) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  const onConfirm = async () => {
    try {
      onStarted(await confirmDeletes.mutateAsync({ runId: run.id, dataTypes: [...picked] }));
    } catch (err) {
      toast({
        title: "Could not confirm the deletes",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <Alert className="border-[color:var(--accent-amber-fg)]/20 bg-[color:var(--accent-amber)]/40">
      <ShieldAlert />
      <AlertTitle className="font-serif text-[1.125rem]">
        {run.is_preview ? "Large deletes would be held" : "Large deletes were held"}
      </AlertTitle>
      <AlertDescription className="space-y-4">
        <p>
          The portal stopped sending many records of these types, so nothing was applied for
          them. That is often a portal glitch. If the portal really removed them, choose the
          types and sync again with their deletes allowed.
        </p>
        <fieldset className="space-y-2">
          <legend className="sr-only">Data types to confirm</legend>
          {issues.map((i) => (
            <label key={i.id} className="flex cursor-pointer items-start gap-3 text-[13.5px]">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 accent-[color:var(--primary)]"
                checked={picked.has(i.data_type)}
                onChange={() => toggle(i.data_type)}
              />
              <span>
                <strong className="font-semibold text-foreground">
                  {DATA_TYPE_LABELS[i.data_type]}
                </strong>
                : {i.message}
              </span>
            </label>
          ))}
        </fieldset>
        <Button
          variant="destructive"
          onClick={onConfirm}
          disabled={locked || !picked.size || confirmDeletes.isPending}
          title={locked ? "Locked while a timetable exists" : undefined}
        >
          {confirmDeletes.isPending ? "Starting…" : "Allow these deletes and sync"}
        </Button>
      </AlertDescription>
    </Alert>
  );
}

const NOUNS: Record<DataType, string> = {
  faculties: "faculty",
  departments: "department",
  classes: "class",
  courses: "course",
  students: "student",
};

/** Delete or keep a record whose delete was held because it is in use
 *  (AC-10). Shown on held deletes of a real sync only. */
function IssueAction({
  issue,
  run,
  locked,
  detached,
}: {
  issue: SyncIssue;
  run: SyncRun;
  locked: boolean;
  detached: boolean;
}) {
  const resolve = useResolveIssue();
  const confirm = useConfirm();
  if (issue.kind !== "delete_held_referenced") return null;
  if (issue.resolution) {
    return (
      <Badge variant="secondary">{issue.resolution === "delete" ? "Deleted" : "Kept"}</Badge>
    );
  }
  if (run.is_preview || detached) return <span className="text-muted-foreground">—</span>;

  const noun = NOUNS[issue.data_type];
  const act = async (resolution: "delete" | "keep") => {
    if (resolution === "delete") {
      const ok = await confirm({
        title: `Delete this ${noun}?`,
        description: `"${issue.row_ref}" and everything that depends on it (exam seats, timetable rows) are removed for good.`,
        confirmLabel: "Delete",
        destructive: true,
      });
      if (!ok) return;
    }
    try {
      await resolve.mutateAsync({ id: issue.id, resolution });
      toast({ title: resolution === "delete" ? `Deleted ${issue.row_ref}` : `Kept ${issue.row_ref}` });
    } catch (err) {
      toast({
        title: "Could not resolve the issue",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <div className="flex justify-end gap-2">
      <Button
        size="sm"
        variant="outline"
        disabled={resolve.isPending}
        onClick={() => act("keep")}
        title={`Keep it as an Ordo ${noun}: later syncs leave it alone`}
      >
        Keep
      </Button>
      <Button
        size="sm"
        variant="destructive"
        disabled={locked || resolve.isPending}
        onClick={() => act("delete")}
        title={locked ? "Locked while a timetable exists" : undefined}
      >
        Delete
      </Button>
    </div>
  );
}
