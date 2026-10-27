import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  ArrowLeft,
  Ban,
  LifeBuoy,
  Loader2,
  Pencil,
  PlayCircle,
  RotateCcw,
  Send,
  Trash2,
  Undo2,
} from "lucide-react";
import {
  FEATURE_LABELS,
  type SchoolDetail,
  usePlatformAudit,
  useSchool,
  useSchoolUsage,
} from "@/api/platform";
import type { MetricRange } from "@/api/telemetry";
import { TrendChart } from "@/components/trend-chart";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { extractErrorEnvelope } from "@/lib/api";
import { orEmpty } from "@/lib/format";
import { isPlatformOwner, usePlatformAuth } from "@/lib/platform-auth";
import { DeleteSchoolDialog, SuspendSchoolDialog } from "./school-closure";
import { StartSupportDialog, SupportSessionsCard } from "./school-support";
import { AuditTable } from "./audit";
import { ErrorGroupsCard, HealthCard } from "./errors";
import { SchoolObservability } from "./observability";
import { SchoolStatusBadge, formatDay, useSchoolActions } from "./schools";

function BackToSchools() {
  return (
    <Link
      to="/console/schools"
      className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-3.5" aria-hidden />
      Schools
    </Link>
  );
}

export interface SchoolSearch {
  tab?: "observability";
  range?: MetricRange;
}

export default function ConsoleSchoolDetailPage({ id, search = {} }: { id: number; search?: SchoolSearch }) {
  const school = useSchool(id);

  if (school.isLoading) {
    return (
      <div className="space-y-8">
        <BackToSchools />
        <Skeleton className="h-12 w-2/3" />
        <div className="grid gap-6 md:grid-cols-2">
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
        </div>
      </div>
    );
  }
  if (school.error || !school.data) {
    return (
      <div className="space-y-8">
        <BackToSchools />
        <Alert variant="destructive">
          <AlertDescription>{extractErrorEnvelope(school.error).detail}</AlertDescription>
        </Alert>
      </div>
    );
  }
  return <SchoolView school={school.data} search={search} />;
}

function SchoolView({ school, search }: { school: SchoolDetail; search: SchoolSearch }) {
  const { admin } = usePlatformAuth();
  const navigate = useNavigate();
  // Telemetry exists once the school's database does (spec 0006).
  const observed = school.status !== "provisioning" && school.status !== "failed";
  const tab = observed && search.tab === "observability" ? "observability" : "overview";
  const setSearch = (patch: SchoolSearch) =>
    void navigate({
      to: "/console/schools/$id",
      params: { id: String(school.id) },
      search: { ...search, ...patch },
      replace: true,
    });
  const actions = useSchoolActions();
  const owner = isPlatformOwner(admin);
  const [suspending, setSuspending] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [supporting, setSupporting] = useState(false);
  const canSupport = school.status === "active" || school.status === "suspended";
  const canDelete = owner && ["active", "suspended", "failed"].includes(school.status);
  const firstAdmin = school.first_admin;
  const canResend = school.status === "active" && firstAdmin !== null && !firstAdmin.joined;
  const features = school.features_override;

  return (
    <div className="space-y-8">
      <BackToSchools />
      <PageHeader
        section="Console · School"
        title={school.name}
        description={school.domain ?? school.slug}
        meta={<SchoolStatusBadge status={school.status} />}
        actions={
          <>
            {owner && school.status === "failed" && (
              <Button variant="brand" disabled={actions.pending} onClick={() => void actions.retry(school)}>
                <RotateCcw data-icon="inline-start" />
                Retry setup
              </Button>
            )}
            {owner && (
              <Link
                to="/console/schools/$id/edit"
                params={{ id: String(school.id) }}
                className={buttonVariants({ variant: "outline" })}
              >
                <Pencil data-icon="inline-start" />
                Edit
              </Link>
            )}
            {canResend && (
              <Button variant="outline" disabled={actions.pending} onClick={() => void actions.resend(school)}>
                <Send data-icon="inline-start" />
                Resend invite
              </Button>
            )}
            {canSupport && (
              <Button variant="outline" onClick={() => setSupporting(true)}>
                <LifeBuoy data-icon="inline-start" />
                Support session
              </Button>
            )}
            {school.status === "active" && (
              <Button variant="outline" onClick={() => setSuspending(true)}>
                <Ban data-icon="inline-start" />
                Suspend
              </Button>
            )}
            {school.status === "suspended" && (
              <Button variant="brand" disabled={actions.pending} onClick={() => void actions.reactivate(school)}>
                <PlayCircle data-icon="inline-start" />
                Reactivate
              </Button>
            )}
            {owner && school.status === "pending_deletion" && (
              <Button variant="brand" disabled={actions.pending} onClick={() => void actions.restore(school)}>
                <Undo2 data-icon="inline-start" />
                Restore
              </Button>
            )}
            {canDelete && (
              <Button variant="destructive" onClick={() => setDeleting(true)}>
                <Trash2 data-icon="inline-start" />
                Delete
              </Button>
            )}
          </>
        }
      />

      {school.status === "provisioning" && (
        <Alert>
          <Loader2 className="animate-spin" aria-hidden />
          <AlertTitle>Setting up</AlertTitle>
          <AlertDescription>
            Ordo is creating this school's database and emailing {school.first_admin_email} an
            invite. This page updates on its own when it's done.
          </AlertDescription>
        </Alert>
      )}
      {school.status === "failed" && (
        <Alert variant="destructive">
          <AlertTitle>Setup failed</AlertTitle>
          <AlertDescription>
            <p>
              {owner
                ? "Retry drops whatever was half built and starts again from an empty database."
                : "Ask an Owner to retry the setup."}
            </p>
            {school.provisioning_error && (
              <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-words bg-[color:var(--destructive)]/5 p-3 font-mono text-[12px]">
                {school.provisioning_error}
              </pre>
            )}
          </AlertDescription>
        </Alert>
      )}

      {school.status === "suspended" && (
        <Alert variant="destructive">
          <AlertTitle>Suspended {school.suspended_at && `since ${formatDay(school.suspended_at)}`}</AlertTitle>
          <AlertDescription>
            <p>
              The school's users see a suspended page until it's reactivated. They aren't told the
              reason.
            </p>
            {school.suspended_reason && (
              <p className="mt-2 whitespace-pre-wrap">Reason: {school.suspended_reason}</p>
            )}
          </AlertDescription>
        </Alert>
      )}
      {school.status === "pending_deletion" && (
        <Alert variant="destructive">
          <AlertTitle>Pending deletion</AlertTitle>
          <AlertDescription>
            <p>
              Users are blocked. The school is removed for good on or after{" "}
              {formatDay(school.purge_after)}
              {owner ? ", unless you restore it before then." : "."}
            </p>
            <p className="mt-2">
              {school.final_export_ref === "never_active"
                ? "It never finished setting up, so there is no data to back up."
                : school.final_export_ref
                  ? "Final backup taken."
                  : "Final backup in progress. It waits for any running job to finish first."}
            </p>
          </AlertDescription>
        </Alert>
      )}
      {school.status === "purged" && (
        <Alert>
          <AlertTitle>Purged {school.purged_at && `on ${formatDay(school.purged_at)}`}</AlertTitle>
          <AlertDescription>
            The school's data, files and address are gone. Its address stays reserved, so it can't
            be given to another school.
          </AlertDescription>
        </Alert>
      )}

      <SuspendSchoolDialog school={school} open={suspending} onOpenChange={setSuspending} />
      <DeleteSchoolDialog school={school} open={deleting} onOpenChange={setDeleting} />
      <StartSupportDialog school={school} open={supporting} onOpenChange={setSupporting} />

      <Tabs
        value={tab}
        onValueChange={(value) => setSearch({ tab: value === "observability" ? "observability" : undefined })}
      >
        {observed && (
          <TabsList variant="line" className="mb-6">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="observability">Observability</TabsTrigger>
          </TabsList>
        )}
        <TabsContent value="overview" className="space-y-8">
          <div className="grid gap-6 md:grid-cols-2">
            <Section title="First administrator">
              <Row label="Name">{school.first_admin_name}</Row>
              <Row label="Email">{school.first_admin_email}</Row>
              <Row label="Status">
                {firstAdmin === null ? (
                  <span className="text-muted-foreground">Invited once setup finishes</span>
                ) : firstAdmin.joined ? (
                  <Badge>Joined</Badge>
                ) : firstAdmin.invite_expires_at &&
                  new Date(firstAdmin.invite_expires_at) > new Date() ? (
                  <span>Invite open until {formatDay(firstAdmin.invite_expires_at)}</span>
                ) : (
                  <Badge variant="warning">Invite expired, resend it</Badge>
                )}
              </Row>
            </Section>

            <Section title="Plan">
              <Row label="Plan">{school.plan.name}</Row>
              <Row label="Student limit">
                {school.max_students_override ?? <span className="text-muted-foreground">Plan's limit</span>}
              </Row>
              <Row label="Staff limit">
                {school.max_staff_users_override ?? <span className="text-muted-foreground">Plan's limit</span>}
              </Row>
              <Row label="Features">
                {features === null ? (
                  <span className="text-muted-foreground">Plan's features</span>
                ) : features.length ? (
                  features.map((f) => FEATURE_LABELS[f]).join(", ")
                ) : (
                  "None"
                )}
              </Row>
              <Row label="Started">{formatDay(school.plan_started_on)}</Row>
              <Row label="Renews">{formatDay(school.renews_on)}</Row>
            </Section>

            <Section title="School">
              <Row label="Address">
                {school.domains.map((d) => (
                  <span key={d.domain} className="block font-mono text-[12.5px]">
                    {d.domain}
                  </span>
                ))}
              </Row>
              <Row label="Time zone">{school.timezone}</Row>
              <Row label="Country">{orEmpty(school.country)}</Row>
              <Row label="Created">
                {formatDay(school.created_at)}
                {school.created_by && (
                  <span className="text-muted-foreground"> by {school.created_by.name}</span>
                )}
              </Row>
            </Section>

            <Section title="Contact">
              <Row label="Name">{orEmpty(school.contact_name)}</Row>
              <Row label="Email">{orEmpty(school.contact_email)}</Row>
              <Row label="Notes">
                {school.notes ? (
                  <span className="whitespace-pre-wrap">{school.notes}</span>
                ) : (
                  <span className="text-muted-foreground">None</span>
                )}
              </Row>
            </Section>
          </div>

          {school.status !== "provisioning" && school.status !== "failed" && (
            <section id="errors" aria-labelledby="errors-heading" className="scroll-mt-24 space-y-6">
              <h2 id="errors-heading" className="font-serif text-[1.75rem] leading-tight tracking-[-0.01em]">
                Errors
              </h2>
              <HealthCard scope={school.id} />
              <ErrorGroupsCard scope={school.id} />
            </section>
          )}

          <UsageCard schoolId={school.id} />
          <SupportSessionsCard schoolId={school.id} />
          <ActivityCard schoolId={school.id} />
        </TabsContent>
        {observed && (
          <TabsContent value="observability">
            <SchoolObservability
              schoolId={school.id}
              range={search.range ?? "24h"}
              onRange={(range) => setSearch({ range: range === "24h" ? undefined : range })}
            />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}

const RANGES = [30, 90, 365] as const;

/** Nightly usage over time against the plan's limits (student count charted). */
function UsageCard({ schoolId }: { schoolId: number }) {
  const [days, setDays] = useState<(typeof RANGES)[number]>(90);
  const usage = useSchoolUsage(schoolId, days);
  const snapshots = usage.data?.snapshots ?? [];
  const latest = snapshots.at(-1);
  const limits = usage.data?.limits;

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Usage</CardTitle>
        <div className="flex gap-1" role="group" aria-label="Range">
          {RANGES.map((r) => (
            <Button
              key={r}
              size="xs"
              variant={r === days ? "default" : "ghost"}
              aria-pressed={r === days}
              onClick={() => setDays(r)}
            >
              {r === 365 ? "1 year" : `${r} days`}
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="space-y-4 px-0">
        {usage.isLoading ? (
          <Skeleton className="mx-5 h-[180px]" />
        ) : (
          <>
            <div className="grid gap-3 px-5 sm:grid-cols-3">
              <UsageMeter label="Students" value={latest?.students} limit={limits?.max_students} />
              <UsageMeter label="Staff users" value={latest?.staff_users} limit={limits?.max_staff_users} />
              <div>
                <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">
                  Last active
                </p>
                <p className="mt-1 text-[14px]">
                  {latest?.last_activity_at ? formatDay(latest.last_activity_at) : "No data yet"}
                </p>
              </div>
            </div>
            <TrendChart
              label="Students"
              limit={limits?.max_students}
              points={snapshots.map((s) => ({
                date: s.date,
                value: s.students,
                details: [
                  ["Staff", s.staff_users],
                  ["Jobs", s.jobs],
                ],
              }))}
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function UsageMeter({ label, value, limit }: { label: string; value?: number; limit?: number | null }) {
  const share = value != null && limit ? Math.min(value / limit, 1) : null;
  const over = value != null && limit != null && value > limit;
  return (
    <div>
      <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">{label}</p>
      <p className={over ? "mt-1 text-[14px] text-destructive" : "mt-1 text-[14px]"}>
        {value == null ? "No data yet" : value.toLocaleString()}
        <span className="text-muted-foreground"> of {limit == null ? "unlimited" : limit.toLocaleString()}</span>
      </p>
      {share != null && (
        <div className="mt-1.5 h-1.5 bg-[color:var(--muted)]">
          <div
            className="h-full"
            style={{
              width: `${share * 100}%`,
              backgroundColor: over || share >= 0.9 ? "var(--destructive)" : "var(--brand-strong)",
            }}
          />
        </div>
      )}
    </div>
  );
}

/** The latest console activity about this school. */
function ActivityCard({ schoolId }: { schoolId: number }) {
  const audit = usePlatformAudit({ school: schoolId, page: 1 });
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Activity</CardTitle>
        <Link
          to="/console/audit"
          search={{ school: schoolId }}
          className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
        >
          View all
        </Link>
      </CardHeader>
      <CardContent className="px-0">
        {audit.isLoading ? (
          <Skeleton className="mx-5 h-24" />
        ) : audit.data?.results.length ? (
          <AuditTable rows={audit.data.results.slice(0, 8)} showSchool={false} />
        ) : (
          <p className="px-5 py-6 text-center text-[13px] text-muted-foreground">Nothing recorded yet.</p>
        )}
      </CardContent>
    </Card>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="divide-y divide-[color:var(--border)]">{children}</dl>
      </CardContent>
    </Card>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_1fr] gap-4 py-2.5 text-[14px]">
      <dt className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground pt-0.5">
        {label}
      </dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}
