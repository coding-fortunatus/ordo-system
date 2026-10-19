import { useState } from "react";
import { Lock, RefreshCw } from "lucide-react";
import {
  type ConnectionMode,
  type EndpointKind,
  type StartedRun,
  usePortalConnection,
  useOrdoFields,
  useStartSync,
  useSyncFinished,
  useSyncRuns,
} from "@/api/portal-sync";
import { useSystemSettings } from "@/api/system";
import { PageHeader } from "@/components/layout/page-header";
import { JobProgressDialog } from "@/components/job-progress-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import { ConnectionCard } from "./connection-card";
import { EndpointCard } from "./endpoint-card";
import { RUN_STATUS_LABELS, RunDetail, RunHistory } from "./runs";

const STEPS = [
  {
    title: "Connect",
    body: "Give Ordo your portal's key. It's stored encrypted and never shown again.",
  },
  {
    title: "Test and map",
    body: "Ordo fetches one page so you can match your columns to its fields.",
  },
  {
    title: "Preview, then sync",
    body: "The first sync is a preview that changes nothing: check its counts and approve it. After that, Ordo applies departments, classes, courses, then students. Each row matches by portal ID, then by its code, name or matric number, and is updated or added. Synced records the portal stops sending are deleted, unless exam data still uses them.",
  },
];

// Apply order. Per type, only the students endpoint is required; a
// combined connection has its one endpoint.
const KINDS: Record<ConnectionMode, EndpointKind[]> = {
  per_type: ["departments", "classes", "courses", "students"],
  combined: ["combined"],
};
const REQUIRED: EndpointKind[] = ["students", "combined"];

export default function PortalSyncPage() {
  const connection = usePortalConnection();
  const fields = useOrdoFields();
  const settings = useSystemSettings();
  const latest = useSyncRuns(1);
  const start = useStartSync();
  const refresh = useSyncFinished();

  const [selectedRun, setSelectedRun] = useState<number | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobOpen, setJobOpen] = useState(false);

  const locked = !!settings.data?.has_timetable;
  const conn = connection.data ?? null;
  const kinds = KINDS[conn?.mode ?? "per_type"];
  const endpointOf = (kind: EndpointKind) => conn?.endpoints.find((e) => e.kind === kind) ?? null;
  const unmapped = kinds.filter((kind) => {
    const e = endpointOf(kind);
    return (
      (REQUIRED.includes(kind) || (e?.is_enabled ?? false)) &&
      (!e || (fields.data?.[kind] ?? []).some((f) => f.required && !e.field_mapping[f.key]))
    );
  });
  const lastRun = latest.data?.results[0];
  const shownRun = selectedRun ?? lastRun?.id ?? null;

  const follow = (r: StartedRun) => {
    setSelectedRun(r.run_id);
    setJobId(r.job_id);
    setJobOpen(true);
  };

  const onSync = async () => {
    try {
      follow(await start.mutateAsync());
    } catch (err) {
      toast({
        title: "Could not start the sync",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  const syncDisabledReason = locked
    ? "Locked while a timetable exists"
    : !conn
      ? "Connect your portal first"
      : unmapped.length > 0
        ? `Map the ${unmapped.join(", ")} endpoint first`
        : null;

  return (
    <div className="space-y-10">
      <PageHeader
        section="Admin · Ingestion"
        title="Portal sync"
        description="Pull your departments, classes, courses and students straight from your school's own portal instead of uploading CSV files."
        meta={<StatusChip connected={!!conn} locked={locked} lastStatus={lastRun?.status} />}
        actions={
          <Button
            onClick={onSync}
            disabled={!!syncDisabledReason || start.isPending}
            title={syncDisabledReason ?? undefined}
          >
            <RefreshCw
              data-icon="inline-start"
              strokeWidth={2}
              className={start.isPending ? "animate-spin" : undefined}
            />
            {start.isPending ? "Starting…" : conn && !conn.approved ? "Preview sync" : "Sync now"}
          </Button>
        }
      />

      {locked && (
        <Alert className="border-[color:var(--accent-yellow-fg)]/20 bg-[color:var(--accent-yellow)]/40">
          <Lock />
          <AlertTitle className="font-serif text-[1.125rem]">Sync is locked</AlertTitle>
          <AlertDescription>
            A timetable exists for this session, so student lists can't change under
            it. Reset the timetable in Settings to sync again.
          </AlertDescription>
        </Alert>
      )}

      {connection.error ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn't load the portal connection</AlertTitle>
          <AlertDescription>{extractErrorEnvelope(connection.error).detail}</AlertDescription>
        </Alert>
      ) : connection.isLoading || fields.isLoading ? (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="space-y-6">
            <Skeleton className="h-72 w-full" />
            <Skeleton className="h-96 w-full" />
          </div>
          <Skeleton className="h-64 w-full" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0 space-y-6">
            <ConnectionCard connection={conn} />
            {conn ? (
              kinds.map((kind) => (
                <EndpointCard
                  key={kind}
                  kind={kind}
                  endpoint={endpointOf(kind)}
                  fields={fields.data?.[kind] ?? []}
                />
              ))
            ) : (
              <div className="rounded-[14px] border border-dashed border-[color:var(--border)] px-6 py-10 text-center text-[13.5px] text-muted-foreground">
                Connect your portal above. Then add the addresses that return your data.
              </div>
            )}
          </div>
          <aside aria-label="How portal sync works" className="space-y-6">
            <HowItWorks />
          </aside>
        </div>
      )}

      {/* History outlives a disconnect (AC-17). */}
      {(conn || lastRun) && (
        <div className="space-y-6">
          <RunHistory selected={shownRun} onSelect={setSelectedRun} />
          {shownRun !== null && (
            <RunDetail
              key={shownRun}
              runId={shownRun}
              connectionId={conn?.id ?? null}
              locked={locked}
              onStarted={follow}
            />
          )}
        </div>
      )}

      <JobProgressDialog
        jobId={jobId}
        title="Syncing from your portal"
        open={jobOpen}
        onOpenChange={(open) => {
          setJobOpen(open);
          if (!open) refresh();
        }}
        onSuccess={() => refresh()}
      />
    </div>
  );
}

function StatusChip({
  connected,
  locked,
  lastStatus,
}: {
  connected: boolean;
  locked: boolean;
  lastStatus?: keyof typeof RUN_STATUS_LABELS;
}) {
  const label = !connected
    ? "Not connected"
    : locked
      ? "Connected · locked"
      : lastStatus
        ? `Connected · last sync ${RUN_STATUS_LABELS[lastStatus].toLowerCase()}`
        : "Connected · never synced";
  const quiet = !connected || locked;
  return (
    <span
      className={
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.14em] " +
        (quiet
          ? "bg-[color:var(--accent-yellow)] text-[color:var(--accent-yellow-fg)]"
          : "bg-[color:var(--accent-green)] text-[color:var(--accent-green-fg)]")
      }
    >
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}

function HowItWorks() {
  return (
    <div className="rounded-[14px] border border-[color:var(--border)] bg-card p-6 shadow-sm ring-1 ring-foreground/5">
      <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
        How it works
      </p>
      <ol className="mt-5 space-y-5">
        {STEPS.map((s, i) => (
          <li key={s.title} className="flex gap-3.5">
            <span className="grid size-6 shrink-0 place-items-center rounded-full bg-[color:var(--accent-iris)] font-mono text-[11px] text-[color:var(--accent-iris-fg)]">
              {i + 1}
            </span>
            <div>
              <p className="text-[14px] font-semibold">{s.title}</p>
              <p className="mt-1 text-[13px] leading-[1.6] text-muted-foreground">{s.body}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="mt-6 border-t border-[color:var(--border)] pt-5 text-[12.5px] leading-[1.6] text-muted-foreground">
        A sync that can't fetch every page changes nothing. Rows it can't use are
        listed under the run's issues, and their records are left as they are.
      </p>
    </div>
  );
}
