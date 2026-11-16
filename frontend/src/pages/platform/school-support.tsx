import { useState, type FormEvent } from "react";
import { ExternalLink } from "lucide-react";
import {
  type SchoolDetail,
  type SupportSessionRow,
  type SupportSessionState,
  useEndSupportSession,
  useStartSupportSession,
  useSupportSessions,
} from "@/api/platform";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
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
  "flex min-h-[72px] w-full border border-transparent border-b-input bg-transparent px-0 py-2 text-base outline-none transition-[color,border-color] placeholder:text-muted-foreground focus-visible:border-b-ring aria-invalid:border-b-destructive md:text-sm";

type School = Pick<SchoolDetail, "id" | "name" | "is_demo" | "support_access">;

/**
 * Start an Ordo support session (spec 0003, child 0006). The school opens in
 * a new tab through a link that works once, within a minute. The reason is
 * written to the school's own audit log.
 *
 * Support can enter a demo school at any time. Any other school must ask
 * first, from its Settings. Until it does, this dialog explains that and
 * shows no form (the server refuses anyway).
 */
export function StartSupportDialog({
  school,
  open,
  onOpenChange,
}: {
  school: School;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const start = useStartSupportSession();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Shown when the browser blocked the new tab.
  const [link, setLink] = useState<string | null>(null);

  const close = (next: boolean) => {
    if (!next) {
      setReason("");
      setError(null);
      setLink(null);
    }
    onOpenChange(next);
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!reason.trim()) {
      setError("Give a reason. The school sees it in its audit log.");
      return;
    }
    setError(null);
    // Opened now, while the click still counts, so popup blockers allow it.
    const tab = window.open("", "_blank");
    try {
      const { url } = await start.mutateAsync({ id: school.id, reason: reason.trim() });
      if (tab) {
        tab.opener = null;
        tab.location.href = url;
        toast({ title: `Support session opened in ${school.name}` });
        close(false);
      } else {
        setLink(url);
      }
    } catch (err) {
      tab?.close();
      const envelope = extractErrorEnvelope(err);
      setError(envelope.errors?.reason?.join(" ") ?? envelope.detail);
    }
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Support {school.name}</DialogTitle>
          <DialogDescription>
            You enter the school as a hidden Super Admin for up to an hour. The school's audit log
            shows that Ordo support came in, why, and everything done during the visit.
          </DialogDescription>
        </DialogHeader>
        {!school.is_demo && !school.support_access ? (
          <>
            <Alert>
              <AlertDescription>
                {school.name} hasn't asked for support. One of its Super Admins can ask from
                Settings in their school, and you can open a session here once they have. Only
                demo schools can be opened without asking.
              </AlertDescription>
            </Alert>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => close(false)}>
                Close
              </Button>
            </DialogFooter>
          </>
        ) : link ? (
          <Alert>
            <AlertDescription>
              <p>Your browser blocked the new tab. This link works once, within a minute.</p>
              <a
                href={link}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() => close(false)}
                className="mt-2 inline-flex items-center gap-1.5 font-medium underline underline-offset-4"
              >
                Open the school
                <ExternalLink className="size-3.5" aria-hidden />
              </a>
            </AlertDescription>
          </Alert>
        ) : (
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            <div className="space-y-2">
              <Label htmlFor="support-reason">Reason</Label>
              <textarea
                id="support-reason"
                rows={3}
                autoFocus
                className={TEXTAREA}
                placeholder="What you're helping with. The school sees this."
                value={reason}
                aria-invalid={error ? true : undefined}
                onChange={(e) => setReason(e.target.value)}
              />
              {error && <p className="text-[13px] text-destructive">{error}</p>}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => close(false)}>
                Cancel
              </Button>
              <Button type="submit" variant="brand" disabled={start.isPending}>
                {start.isPending ? "Opening…" : "Open the school"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

const STATE_BADGES: Record<
  SupportSessionState,
  { label: string; variant: "default" | "secondary" | "warning" }
> = {
  active: { label: "Active", variant: "warning" },
  waiting: { label: "Link not used yet", variant: "secondary" },
  unused: { label: "Link expired", variant: "secondary" },
  ended: { label: "Ended", variant: "secondary" },
};

export function formatSupportTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** This school's support sessions, newest first; open ones can be ended. */
export function SupportSessionsCard({ schoolId }: { schoolId: number }) {
  const sessions = useSupportSessions(schoolId);
  const end = useEndSupportSession();
  const { admin } = usePlatformAuth();
  const owner = isPlatformOwner(admin);

  if (!sessions.data?.length) return null;

  const canEnd = (row: SupportSessionRow) =>
    (row.state === "active" || row.state === "waiting") && (owner || row.admin.id === admin?.id);

  const onEnd = async (row: SupportSessionRow) => {
    try {
      await end.mutateAsync(row.id);
      toast({ title: "Support session ended" });
    } catch (err) {
      toast({ title: extractErrorEnvelope(err).detail, variant: "destructive" });
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Support sessions</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Admin</TableHead>
              <TableHead>Reason</TableHead>
              <TableHead>State</TableHead>
              <TableHead>Started</TableHead>
              <TableHead>
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sessions.data.map((row) => {
              const badge = STATE_BADGES[row.state];
              return (
                <TableRow key={row.id}>
                  <TableCell>{row.admin.name}</TableCell>
                  <TableCell className="max-w-[320px] whitespace-pre-wrap break-words">
                    {row.reason}
                  </TableCell>
                  <TableCell>
                    <Badge variant={badge.variant}>{badge.label}</Badge>
                  </TableCell>
                  <TableCell className="whitespace-nowrap font-mono text-[12px] text-muted-foreground">
                    {formatSupportTime(row.started_at ?? row.created_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    {canEnd(row) && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={end.isPending}
                        onClick={() => void onEnd(row)}
                      >
                        End
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
