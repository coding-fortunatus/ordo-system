import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  ArrowUpRight,
  CalendarClock,
  CircleCheck,
  GraduationCap,
  LifeBuoy,
  ListChecks,
  type LucideIcon,
  Plus,
  School,
  Trash2,
  UserRound,
  PauseCircle,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import {
  type AttentionKind,
  type PlatformOverview,
  type SchoolStatus,
  usePlatformOverview,
} from "@/api/platform";
import { PageHeader } from "@/components/layout/page-header";
import { TrendChart } from "@/components/trend-chart";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { extractErrorEnvelope } from "@/lib/api";
import { isPlatformOwner, usePlatformAuth } from "@/lib/platform-auth";
import { cn } from "@/lib/utils";
import { ACTION_LABELS } from "./audit";
import { SchoolStatusBadge, formatDay } from "./schools";

const LABEL = "font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground";

function shortDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function timeAgo(iso: string) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return shortDate(iso);
}

/** A square bordered panel with a title row, the dashboard's building block. */
function Panel({
  title,
  action,
  className,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn("flex flex-col border border-[color:var(--border)] bg-card", className)}>
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-[color:var(--border)] px-5">
        <h2 className="text-[13.5px] font-medium">{title}</h2>
        {action}
      </div>
      <div className="flex-1">{children}</div>
    </section>
  );
}

function PanelLink({ to, children }: { to: "/console/schools" | "/console/audit" | "/console/plans"; children: string }) {
  return (
    <Link
      to={to}
      className="group inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
    >
      {children}
      <ArrowUpRight className="size-3 transition-transform group-hover:-translate-y-px group-hover:translate-x-px" />
    </Link>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-5 py-8 text-center text-[13px] text-muted-foreground">{children}</p>;
}

/* ---------------------------------------------------------------- tiles */

function StatTile({
  label,
  value,
  hint,
  icon: Icon,
  loading,
}: {
  label: string;
  value: number | undefined;
  hint?: string;
  icon: LucideIcon;
  loading: boolean;
}) {
  return (
    <div className="flex flex-col justify-between gap-5 border border-[color:var(--border)] bg-card p-5">
      <div className="flex items-start justify-between">
        <span className={LABEL}>{label}</span>
        <span
          className="grid size-7 place-items-center"
          style={{ backgroundColor: "var(--brand-soft)", color: "var(--brand-strong)" }}
        >
          <Icon className="size-3.5" strokeWidth={2} aria-hidden />
        </span>
      </div>
      {loading ? (
        <Skeleton className="h-10 w-24" />
      ) : (
        <div>
          <div className="font-serif text-[2.5rem] leading-none tabular-nums tracking-[-0.02em]">
            {(value ?? 0).toLocaleString()}
          </div>
          {hint && <p className="mt-2 text-[12px] text-muted-foreground">{hint}</p>}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- backup */

function formatBytes(bytes: number | null) {
  if (bytes == null) return "size unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function BackupHealth({ backup }: { backup: PlatformOverview["backup"] }) {
  const healthy = !backup.stale;
  return (
    <div className="space-y-4 px-5 py-4">
      <div className="flex items-start gap-3">
        {healthy ? (
          <ShieldCheck className="mt-0.5 size-4 shrink-0 text-[color:var(--brand-strong)]" aria-hidden />
        ) : (
          <ShieldAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
        )}
        <div className="text-[13px]">
          <p className="font-medium">
            {healthy ? "Backed up" : backup.last_success ? "Backup is overdue" : "No backup yet"}
          </p>
          <p className="text-muted-foreground">
            {backup.last_success
              ? `Last good backup ${timeAgo(backup.last_success.started_at)}, ${formatBytes(backup.last_success.size_bytes)}`
              : backup.configured
                ? "The backup service hasn't reported a successful run."
                : "BACKUP_BUCKET isn't set, so backups aren't running."}
          </p>
          {backup.last_run && !backup.last_run.ok && (
            <p className="mt-1 line-clamp-2 font-mono text-[11px] text-destructive">
              Last attempt failed: {backup.last_run.error.split("\n").filter(Boolean).at(-1)}
            </p>
          )}
        </div>
      </div>
      {backup.recent.length > 0 && (
        <div>
          <p className={cn(LABEL, "mb-1.5")}>Last {backup.recent.length} attempts</p>
          <ul className="flex gap-1" aria-label="Recent backup attempts, oldest first">
            {backup.recent.map((run) => (
              <li
                key={run.started_at}
                title={`${formatDay(run.started_at)}: ${run.ok ? `ok, ${formatBytes(run.size_bytes)}` : "failed"}`}
                className={cn("h-5 flex-1", run.ok ? "bg-[color:var(--brand-strong)]" : "bg-destructive")}
              >
                <span className="sr-only">
                  {formatDay(run.started_at)} {run.ok ? "succeeded" : "failed"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- lists */

const STATUS_ORDER: SchoolStatus[] = [
  "active",
  "provisioning",
  "suspended",
  "pending_deletion",
  "failed",
  "purged",
];

function StatusBreakdown({ byStatus }: { byStatus: Record<SchoolStatus, number> }) {
  const rows = STATUS_ORDER.filter((s) => byStatus[s] > 0);
  if (!rows.length) return <Empty>No schools yet.</Empty>;
  return (
    <ul className="divide-y divide-[color:var(--border)]">
      {rows.map((status) => (
        <li key={status} className="flex items-center justify-between px-5 py-3">
          <SchoolStatusBadge status={status} />
          <span className="font-mono text-[13px] tabular-nums">{byStatus[status]}</span>
        </li>
      ))}
    </ul>
  );
}

const ATTENTION: Record<AttentionKind, { icon: LucideIcon; text: (at: string | null) => string; tone: string }> = {
  failed: { icon: AlertTriangle, text: () => "Setup failed", tone: "text-destructive" },
  pending_deletion: {
    icon: Trash2,
    text: (at) => (at ? `Data purged ${formatDay(at)}` : "Pending deletion"),
    tone: "text-destructive",
  },
  suspended: {
    icon: PauseCircle,
    text: (at) => (at ? `Suspended ${formatDay(at)}` : "Suspended"),
    tone: "text-[color:var(--accent-amber-fg)]",
  },
  renewal_overdue: {
    icon: CalendarClock,
    text: (at) => `Renewal lapsed ${formatDay(at)}`,
    tone: "text-destructive",
  },
  renewal_due: {
    icon: CalendarClock,
    text: (at) => `Renews ${formatDay(at)}`,
    tone: "text-[color:var(--accent-amber-fg)]",
  },
};

function AttentionList({ items }: { items: PlatformOverview["attention"] }) {
  if (!items.length) {
    return (
      <div className="flex items-center justify-center gap-2 px-5 py-8 text-[13px] text-muted-foreground">
        <CircleCheck className="size-4" aria-hidden />
        Nothing needs attention.
      </div>
    );
  }
  return (
    <ul className="divide-y divide-[color:var(--border)]">
      {items.map((item) => {
        const { icon: Icon, text, tone } = ATTENTION[item.kind];
        return (
          <li key={`${item.kind}-${item.school.id}`}>
            <Link
              to="/console/schools/$id"
              params={{ id: String(item.school.id) }}
              className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-[color:var(--muted)]/50"
            >
              <Icon className={cn("size-4 shrink-0", tone)} aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13.5px] font-medium">{item.school.name}</span>
                <span className="block truncate font-mono text-[11px] text-muted-foreground">
                  {item.school.slug}
                </span>
              </span>
              <span className={cn("shrink-0 text-[12.5px]", tone)}>{text(item.at)}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function SupportList({ sessions }: { sessions: PlatformOverview["support"] }) {
  if (!sessions.length) return <Empty>No one is in a school right now.</Empty>;
  return (
    <ul className="divide-y divide-[color:var(--border)]">
      {sessions.map((s) => (
        <li key={s.id} className="px-5 py-3">
          <div className="flex items-center justify-between gap-3">
            <Link
              to="/console/schools/$id"
              params={{ id: String(s.school.id) }}
              className="truncate text-[13.5px] font-medium hover:underline"
            >
              {s.school.name}
            </Link>
            <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
              until{" "}
              {new Date(s.expires_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
            </span>
          </div>
          <p className="mt-0.5 truncate text-[12.5px] text-muted-foreground">
            {s.admin.name}: {s.reason}
          </p>
        </li>
      ))}
    </ul>
  );
}

function PlanBars({ plans }: { plans: PlatformOverview["plans"] }) {
  if (!plans.length) return <Empty>No plans yet.</Empty>;
  const max = Math.max(...plans.map((p) => p.schools), 1);
  return (
    <ul className="space-y-3 px-5 py-4">
      {plans.map((plan) => (
        <li key={plan.id} title={`${plan.name}: ${plan.schools} schools`}>
          <div className="mb-1 flex justify-between text-[12.5px]">
            <span>{plan.name}</span>
            <span className="font-mono tabular-nums text-muted-foreground">{plan.schools}</span>
          </div>
          <div className="h-1.5 w-full bg-[color:var(--muted)]">
            <div
              className="h-full"
              style={{
                width: `${(plan.schools / max) * 100}%`,
                backgroundColor: "var(--brand-strong)",
              }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

function RecentActivity({ rows }: { rows: PlatformOverview["recent_activity"] }) {
  if (!rows.length) return <Empty>Nothing recorded yet.</Empty>;
  return (
    <ul className="divide-y divide-[color:var(--border)]">
      {rows.map((row) => (
        <li key={row.id} className="flex items-start gap-3 px-5 py-3">
          <UserRound className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <div className="min-w-0 flex-1 text-[13px]">
            <span className="font-medium">{row.admin?.name ?? "System"}</span>{" "}
            <span className="text-muted-foreground">
              {(ACTION_LABELS[row.action] ?? row.action.replaceAll("_", " ")).toLowerCase()}
            </span>
            {row.school && <span className="font-medium"> · {row.school.name}</span>}
          </div>
          <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{timeAgo(row.created_at)}</span>
        </li>
      ))}
    </ul>
  );
}

/* ---------------------------------------------------------------- page */

/** The console's home: platform health at a glance. */
export default function ConsoleDashboardPage() {
  const { admin } = usePlatformAuth();
  const overview = usePlatformOverview();
  const data = overview.data;
  const loading = overview.isLoading;
  const asOf = data?.usage.as_of ? `As of ${formatDay(data.usage.as_of)}` : "No snapshot yet";

  return (
    <div className="space-y-8">
      <PageHeader
        section="Console · Platform"
        title="Dashboard"
        description="Every school on Ordo at a glance: who is using it, what needs a look, and what changed."
        actions={
          isPlatformOwner(admin) && (
            <Link to="/console/schools/new" className={buttonVariants({ variant: "brand" })}>
              <Plus data-icon="inline-start" />
              New school
            </Link>
          )
        }
      />

      {overview.error && (
        <Alert variant="destructive">
          <AlertDescription>{extractErrorEnvelope(overview.error).detail}</AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Schools"
          icon={School}
          loading={loading}
          value={data?.schools.total}
          hint={data && `${data.schools.new_last_30_days} new in 30 days`}
        />
        <StatTile label="Students" icon={GraduationCap} loading={loading} value={data?.usage.students} hint={asOf} />
        <StatTile label="Staff users" icon={UserRound} loading={loading} value={data?.usage.staff_users} hint={asOf} />
        <StatTile
          label="Jobs in 24h"
          icon={ListChecks}
          loading={loading}
          value={data?.usage.jobs_last_24h}
          hint={asOf}
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <Panel title="Students across all schools" className="lg:col-span-2" action={<span className={LABEL}>30 days</span>}>
          {loading ? (
            <Skeleton className="m-5 h-[180px]" />
          ) : (
            <TrendChart
              label="Students"
              points={(data?.trend ?? []).map((d) => ({
                date: d.date,
                value: d.students,
                details: [
                  ["Staff", d.staff_users],
                  ["Jobs", d.jobs],
                ],
              }))}
            />
          )}
        </Panel>
        <Panel title="Schools by status" action={<PanelLink to="/console/schools">All schools</PanelLink>}>
          {loading ? <Skeleton className="m-5 h-32" /> : data && <StatusBreakdown byStatus={data.schools.by_status} />}
        </Panel>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <Panel
          title="Needs attention"
          className="lg:col-span-2"
          action={data && data.attention.length > 0 && <span className={LABEL}>{data.attention.length}</span>}
        >
          {loading ? <Skeleton className="m-5 h-32" /> : <AttentionList items={data?.attention ?? []} />}
        </Panel>
        <Panel
          title="Support sessions"
          action={<LifeBuoy className="size-3.5 text-muted-foreground" aria-hidden />}
        >
          {loading ? <Skeleton className="m-5 h-20" /> : <SupportList sessions={data?.support ?? []} />}
        </Panel>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <div className="grid content-start gap-3">
          <Panel title="Database backup">
            {loading ? <Skeleton className="m-5 h-24" /> : data && <BackupHealth backup={data.backup} />}
          </Panel>
          <Panel title="Schools per plan" action={<PanelLink to="/console/plans">Plans</PanelLink>}>
            {loading ? <Skeleton className="m-5 h-24" /> : <PlanBars plans={data?.plans ?? []} />}
          </Panel>
        </div>
        <Panel
          title="Recent activity"
          className="lg:col-span-2"
          action={<PanelLink to="/console/audit">Audit log</PanelLink>}
        >
          {loading ? <Skeleton className="m-5 h-40" /> : <RecentActivity rows={data?.recent_activity ?? []} />}
        </Panel>
      </div>
    </div>
  );
}
