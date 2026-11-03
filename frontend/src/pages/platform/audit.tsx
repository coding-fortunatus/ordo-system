import { useState } from "react";
import { X } from "lucide-react";
import { type PlatformAuditRow, usePlatformAdmins, usePlatformAudit } from "@/api/platform";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { isPlatformOwner, usePlatformAuth } from "@/lib/platform-auth";
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
import { orEmpty } from "@/lib/format";

/** Readable names for the actions the console records. */
export const ACTION_LABELS: Record<string, string> = {
  login: "Signed in",
  logout: "Signed out",
  login_failed: "Wrong password",
  totp_failed: "Wrong code",
  totp_enrolled: "Set up two factor sign in",
  admin_invited: "Invited an admin",
  admin_invite_resent: "Resent an admin invite",
  admin_accepted: "Accepted their invite",
  admin_updated: "Changed an admin",
  admin_2fa_reset: "Reset an admin's two factor sign in",
  unlocked: "Unlocked an admin's sign in",
  school_created: "Created a school",
  school_provisioned: "School set up",
  school_provisioning_failed: "School setup failed",
  school_provisioning_retried: "Retried a school's setup",
  school_invite_resent: "Resent a school's first admin invite",
  school_updated: "Changed a school",
  school_suspended: "Suspended a school",
  school_reactivated: "Reactivated a school",
  school_deletion_requested: "Asked to delete a school",
  school_restored: "Restored a school",
  school_exported: "School backed up for deletion",
  export_failed: "School backup failed",
  school_purged: "School data purged",
  plan_created: "Created a plan",
  plan_updated: "Changed a plan",
  support_ticket_issued: "Opened a support link",
  support_started: "Entered a school as support",
  support_ended: "Ended a support session",
  password_changed: "Changed their password",
  account_password_wrong: "Wrong password on the account page",
  totp_reenrolled: "Moved to a new authenticator",
  session_revoked: "Ended one of their sessions",
  sessions_revoked: "Signed out everywhere else",
  admin_signed_out: "Signed an admin out everywhere",
  staff_reindex_requested: "Rebuilt the staff index",
  schools_exported: "Exported the schools list",
  announcement_created: "Made an announcement",
  announcement_withdrawn: "Withdrew an announcement",
};

/** Written by background work, not by an admin. */
const SYSTEM_ACTIONS = new Set([
  "school_provisioned",
  "school_provisioning_failed",
  "school_exported",
  "export_failed",
  "school_purged",
]);

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Who an entry is about, when it isn't the admin who acted. */
function subject(detail: Record<string, unknown>): string | null {
  return typeof detail.email === "string" ? detail.email : null;
}

/** The audit rows as a table; the audit page and a school's activity share it. */
export function AuditTable({ rows, showSchool = true }: { rows: PlatformAuditRow[]; showSchool?: boolean }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[180px]">When</TableHead>
          <TableHead>Admin</TableHead>
          <TableHead>Action</TableHead>
          {showSchool && <TableHead>School</TableHead>}
          <TableHead className="w-[140px]">IP</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const about = subject(row.detail);
          return (
            <TableRow key={row.id}>
              <TableCell className="whitespace-nowrap font-mono text-[12px] text-muted-foreground">
                {formatTimestamp(row.created_at)}
              </TableCell>
              <TableCell>
                <span className="block font-medium">
                  {row.admin?.name ?? (SYSTEM_ACTIONS.has(row.action) ? "Ordo" : "Unknown")}
                </span>
                {row.admin && (
                  <span className="block text-[12px] text-muted-foreground">{row.admin.email}</span>
                )}
              </TableCell>
              <TableCell>
                <span className="block">{ACTION_LABELS[row.action] ?? row.action}</span>
                {about && about !== row.admin?.email && (
                  <span className="block font-mono text-[11px] text-muted-foreground">{about}</span>
                )}
              </TableCell>
              {showSchool && <TableCell>{orEmpty(row.school?.name)}</TableCell>}
              <TableCell className="font-mono text-[12px] text-muted-foreground">
                {orEmpty(row.ip)}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

const ANY = "any";

export interface AuditFilters {
  action?: string;
  admin?: number;
  school?: number;
}

export default function ConsoleAuditPage({
  filters,
  onFiltersChange,
}: {
  filters: AuditFilters;
  onFiltersChange: (next: AuditFilters) => void;
}) {
  const { admin: me } = usePlatformAuth();
  const owner = isPlatformOwner(me);
  const [page, setPage] = useState(1);
  const list = usePlatformAudit({ page, ...filters });
  // Only Owners can list admins; Support filters by action and school.
  const admins = usePlatformAdmins({ enabled: owner });
  const schoolName = list.data?.results.find((r) => r.school?.id === filters.school)?.school?.name;
  const filtered = Boolean(filters.action || filters.admin || filters.school);

  const set = (next: AuditFilters) => {
    setPage(1);
    onFiltersChange({ ...filters, ...next });
  };

  return (
    <ListShell
      section="Console · Manage"
      title="Audit log"
      description="Every console sign in, failed attempt and change, with who did it, when and from where."
      filters={
        <>
          <Select value={filters.action ?? ANY} onValueChange={(v) => set({ action: v === ANY ? undefined : (v as string) })}>
            <SelectTrigger className="h-8 w-[220px] text-[12px]" aria-label="Filter by action">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Any action</SelectItem>
              {Object.entries(ACTION_LABELS)
                .sort(([, a], [, b]) => a.localeCompare(b))
                .map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
          {owner && (
            <Select
              value={filters.admin ? String(filters.admin) : ANY}
              onValueChange={(v) => set({ admin: v === ANY ? undefined : Number(v) })}
            >
              <SelectTrigger className="h-8 w-[180px] text-[12px]" aria-label="Filter by admin">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Any admin</SelectItem>
                {admins.data?.map((a) => (
                  <SelectItem key={a.id} value={String(a.id)}>
                    {a.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {filters.school && (
            <Button variant="outline" size="xs" onClick={() => set({ school: undefined })}>
              School: {schoolName ?? `#${filters.school}`}
              <X data-icon="inline-end" />
            </Button>
          )}
        </>
      }
      isLoading={list.isLoading}
      error={list.error}
      isEmpty={!list.data?.results.length}
      emptyMessage={filtered ? "Nothing matches these filters." : "Nothing recorded yet."}
      pagination={
        list.data && (
          <PaginationFooter page={page} pageSize={15} total={list.data.count} onPageChange={setPage} />
        )
      }
    >
      <AuditTable rows={list.data?.results ?? []} />
    </ListShell>
  );
}
