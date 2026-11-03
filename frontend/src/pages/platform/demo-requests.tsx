import { useEffect, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Building2, Mail, Phone } from "lucide-react";
import {
  DEMO_REQUEST_KINDS,
  DEMO_REQUEST_STATUSES,
  STUDENT_COUNT_LABELS,
  type DemoRequestKind,
  type DemoRequestRow,
  type DemoRequestStatus,
  useDemoRequest,
  useDemoRequests,
  usePlatformAdmins,
  useUpdateDemoRequest,
} from "@/api/platform";
import { ListShell } from "@/components/data-table/list-shell";
import { PaginationFooter } from "@/components/data-table/pagination";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
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
  SheetFooter,
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
import { isPlatformOwner, usePlatformAuth } from "@/lib/platform-auth";
import { toast } from "@/lib/use-toast";

const TEXTAREA =
  "flex min-h-[120px] w-full border border-transparent border-b-input bg-transparent px-0 py-2 text-base outline-none transition-[color,border-color] placeholder:text-muted-foreground focus-visible:border-b-ring md:text-sm";

const STATUS_TONES: Record<DemoRequestStatus, "default" | "secondary" | "warning"> = {
  new: "warning",
  contacted: "default",
  scheduled: "default",
  converted: "secondary",
  closed: "secondary",
};

const ALL = "all";
const OPEN = "open";
type StatusFilter = DemoRequestStatus | typeof ALL | typeof OPEN;
const UNASSIGNED = "none";

function formatWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function StatusBadge({ status }: { status: DemoRequestStatus }) {
  return <Badge variant={STATUS_TONES[status]}>{DEMO_REQUEST_STATUSES[status]}</Badge>;
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-0.5">
      <dt className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">{label}</dt>
      <dd className="text-[13.5px]">{children}</dd>
    </div>
  );
}

/** One request: what they asked, and the team's status, owner and notes. */
function RequestSheet({ id, onClose }: { id: number | null; onClose: () => void }) {
  const { admin } = usePlatformAuth();
  const request = useDemoRequest(id ?? undefined);
  const admins = usePlatformAdmins({ enabled: isPlatformOwner(admin) });
  const update = useUpdateDemoRequest();
  const data = request.data;

  const [status, setStatus] = useState<DemoRequestStatus>("new");
  const [assignee, setAssignee] = useState<string>(UNASSIGNED);
  const [notes, setNotes] = useState("");
  useEffect(() => {
    if (!data) return;
    setStatus(data.status);
    setAssignee(data.assigned_to ? String(data.assigned_to.id) : UNASSIGNED);
    setNotes(data.notes);
  }, [data]);

  // Support admins can't list admins, so they can take a request or let it go.
  const assignable =
    admins.data?.filter((a) => a.is_active && !a.invite_pending).map((a) => ({ id: a.id, name: a.name })) ??
    (admin ? [{ id: admin.id, name: admin.name }] : []);
  if (data?.assigned_to && !assignable.some((a) => a.id === data.assigned_to!.id)) {
    assignable.push(data.assigned_to);
  }

  const dirty =
    data !== undefined &&
    (status !== data.status ||
      notes !== data.notes ||
      assignee !== (data.assigned_to ? String(data.assigned_to.id) : UNASSIGNED));

  const save = async () => {
    if (!data) return;
    try {
      await update.mutateAsync({
        id: data.id,
        status,
        notes,
        assigned_to_id: assignee === UNASSIGNED ? null : Number(assignee),
      });
      toast({ title: "Request updated" });
    } catch (err) {
      toast({ title: "Couldn't save", description: extractErrorEnvelope(err).detail, variant: "destructive" });
    }
  };

  return (
    <Sheet open={id !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto data-[side=right]:sm:max-w-xl">
        {!data ? (
          <div className="space-y-4 p-6">
            <SheetTitle className="sr-only">Loading request</SheetTitle>
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-40" />
          </div>
        ) : (
          <>
            <SheetHeader>
              <div className="flex items-center gap-2">
                <Badge variant="outline">{DEMO_REQUEST_KINDS[data.kind]}</Badge>
                <StatusBadge status={data.status} />
              </div>
              <SheetTitle>{data.institution}</SheetTitle>
              <SheetDescription>Received {formatWhen(data.created_at)}</SheetDescription>
            </SheetHeader>

            <div className="space-y-6 px-6">
              <dl className="grid gap-4 sm:grid-cols-2">
                <Detail label="Name">{data.name}</Detail>
                <Detail label="Role">{data.job_title || "—"}</Detail>
                <Detail label="Email">
                  <a href={`mailto:${data.email}`} className="hover:underline">
                    {data.email}
                  </a>
                </Detail>
                <Detail label="Phone">
                  {data.phone ? (
                    <a href={`tel:${data.phone.replace(/\s+/g, "")}`} className="hover:underline">
                      {data.phone}
                    </a>
                  ) : (
                    "—"
                  )}
                </Detail>
                <Detail label="Students">
                  {STUDENT_COUNT_LABELS[data.student_count] ?? (data.student_count || "—")}
                </Detail>
                <Detail label="School">
                  {data.school ? (
                    <Link
                      to="/console/schools/$id"
                      params={{ id: String(data.school.id) }}
                      className="hover:underline"
                    >
                      {data.school.name}
                    </Link>
                  ) : (
                    "Not created yet"
                  )}
                </Detail>
              </dl>

              {data.message && (
                <Detail label="Message">
                  <p className="whitespace-pre-line text-muted-foreground">{data.message}</p>
                </Detail>
              )}

              <div className="flex flex-wrap gap-2">
                <a
                  href={`mailto:${data.email}?subject=${encodeURIComponent(`Your Ordo ${data.kind === "setup" ? "setup" : "demo"}`)}`}
                  className={buttonVariants({ variant: "outline", size: "sm" })}
                >
                  <Mail data-icon="inline-start" />
                  Email
                </a>
                {data.phone && (
                  <a
                    href={`tel:${data.phone.replace(/\s+/g, "")}`}
                    className={buttonVariants({ variant: "outline", size: "sm" })}
                  >
                    <Phone data-icon="inline-start" />
                    Call
                  </a>
                )}
                {isPlatformOwner(admin) && !data.school && (
                  <Link
                    to="/console/schools/new"
                    search={{ request: data.id }}
                    className={buttonVariants({ variant: "brand", size: "sm" })}
                  >
                    <Building2 data-icon="inline-start" />
                    Create school
                  </Link>
                )}
              </div>

              <div className="grid gap-4 border-t border-[color:var(--border)] pt-6 sm:grid-cols-2">
                <div className="grid gap-2">
                  <Label htmlFor="request-status">Status</Label>
                  <Select value={status} onValueChange={(v) => setStatus(v as DemoRequestStatus)}>
                    <SelectTrigger id="request-status">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {(Object.keys(DEMO_REQUEST_STATUSES) as DemoRequestStatus[]).map((s) => (
                        <SelectItem key={s} value={s}>
                          {DEMO_REQUEST_STATUSES[s]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="request-owner">Owner</Label>
                  <Select value={assignee} onValueChange={(v) => setAssignee(v as string)}>
                    <SelectTrigger id="request-owner">
                      <SelectValue>
                        {(v: string) =>
                          v === UNASSIGNED
                            ? "Unassigned"
                            : (assignable.find((a) => String(a.id) === v)?.name ?? "Unassigned")
                        }
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={UNASSIGNED}>Unassigned</SelectItem>
                      {assignable.map((a) => (
                        <SelectItem key={a.id} value={String(a.id)}>
                          {a.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2 sm:col-span-2">
                  <Label htmlFor="request-notes">Notes</Label>
                  <textarea
                    id="request-notes"
                    className={TEXTAREA}
                    placeholder="Call booked for Tuesday 10:00. Wants seating and timetable."
                    maxLength={5000}
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                  />
                  <p className="text-[12px] text-muted-foreground">Only the Ordo team sees these.</p>
                </div>
              </div>
            </div>

            <SheetFooter>
              <Button variant="brand" disabled={!dirty || update.isPending} onClick={save}>
                Save
              </Button>
            </SheetFooter>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

/** Demo and setup help requests from the marketing site. */
export default function ConsoleDemoRequestsPage() {
  const navigate = useNavigate();
  const { id } = useSearch({ from: "/_platform/_console/console/requests" });
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>(OPEN);
  const [kind, setKind] = useState<DemoRequestKind | typeof ALL>(ALL);
  const list = useDemoRequests({
    page,
    q: query || undefined,
    status: status === ALL ? undefined : status,
    kind: kind === ALL ? undefined : kind,
  });
  const filtered = Boolean(query) || status !== OPEN || kind !== ALL;
  const open = (next: number | undefined) =>
    navigate({ to: "/console/requests", search: next === undefined ? {} : { id: next }, replace: true });

  return (
    <>
      <ListShell
        section="Console · Platform"
        title="Requests"
        description="Demo and setup help requests sent from the Ordo website. Owners get an email for each new one."
        query={query}
        onQueryChange={(q) => {
          setQuery(q);
          setPage(1);
        }}
        searchPlaceholder="Search by name, email or institution"
        filters={
          <>
            <Select
              value={status}
              onValueChange={(v) => {
                setStatus(v as StatusFilter);
                setPage(1);
              }}
            >
              <SelectTrigger className="h-8 w-[150px] text-[12px]" aria-label="Filter by status">
                <SelectValue>
                  {(v: string) =>
                    v === OPEN ? "Open" : v === ALL ? "Any status" : DEMO_REQUEST_STATUSES[v as DemoRequestStatus]
                  }
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={OPEN}>Open</SelectItem>
                <SelectItem value={ALL}>Any status</SelectItem>
                {(Object.keys(DEMO_REQUEST_STATUSES) as DemoRequestStatus[]).map((s) => (
                  <SelectItem key={s} value={s}>
                    {DEMO_REQUEST_STATUSES[s]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={kind}
              onValueChange={(v) => {
                setKind(v as DemoRequestKind | typeof ALL);
                setPage(1);
              }}
            >
              <SelectTrigger className="h-8 w-[140px] text-[12px]" aria-label="Filter by type">
                <SelectValue>
                  {(v: string) => (v === ALL ? "Any type" : DEMO_REQUEST_KINDS[v as DemoRequestKind])}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>Any type</SelectItem>
                {(Object.keys(DEMO_REQUEST_KINDS) as DemoRequestKind[]).map((k) => (
                  <SelectItem key={k} value={k}>
                    {DEMO_REQUEST_KINDS[k]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>
        }
        isLoading={list.isLoading}
        error={list.error}
        isEmpty={!list.data?.results.length}
        emptyMessage={
          filtered
            ? "No requests match these filters."
            : "Nothing waiting. New requests from the website land here."
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
              <TableHead>Institution</TableHead>
              <TableHead>Contact</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Owner</TableHead>
              <TableHead>Received</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.results.map((row: DemoRequestRow) => (
              <TableRow key={row.id} className="cursor-pointer" onClick={() => open(row.id)}>
                <TableCell>
                  <button type="button" className="font-medium hover:underline" onClick={() => open(row.id)}>
                    {row.institution}
                  </button>
                  {row.student_count && (
                    <span className="block font-mono text-[11.5px] text-muted-foreground">
                      {STUDENT_COUNT_LABELS[row.student_count] ?? row.student_count} students
                    </span>
                  )}
                </TableCell>
                <TableCell>
                  <span className="block">{row.name}</span>
                  <span className="block font-mono text-[11.5px] text-muted-foreground">{row.email}</span>
                </TableCell>
                <TableCell>{DEMO_REQUEST_KINDS[row.kind]}</TableCell>
                <TableCell>
                  <StatusBadge status={row.status} />
                </TableCell>
                <TableCell className="text-muted-foreground">{row.assigned_to?.name ?? "—"}</TableCell>
                <TableCell className="font-mono text-[12px] text-muted-foreground">
                  {formatWhen(row.created_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </ListShell>
      <RequestSheet id={id ?? null} onClose={() => open(undefined)} />
    </>
  );
}
