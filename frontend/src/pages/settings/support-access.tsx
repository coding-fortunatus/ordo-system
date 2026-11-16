import { useState, type FormEvent } from "react";
import {
  useRequestSupport,
  useSchoolPlan,
  useSupportAccess,
  useWithdrawSupport,
} from "@/api/school";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { extractErrorEnvelope } from "@/lib/api";
import { useConfirm } from "@/lib/confirm";
import { toast } from "@/lib/use-toast";

const TEXTAREA =
  "flex min-h-[72px] w-full border border-transparent border-b-input bg-transparent px-0 py-2 text-base outline-none transition-[color,border-color] placeholder:text-muted-foreground focus-visible:border-b-ring aria-invalid:border-b-destructive md:text-sm";

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Let Ordo support into this school. Support can't come in until a Super
 * Admin asks here. The request lasts a day, and withdrawing it ends any
 * visit at once. Hidden during a support visit: support can't let itself in.
 */
export function SupportAccessCard() {
  const plan = useSchoolPlan();
  const inSupport = Boolean(plan.data?.support);
  const access = useSupportAccess(plan.isSuccess && !inSupport);
  const request = useRequestSupport();
  const withdraw = useWithdrawSupport();
  const confirm = useConfirm();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (inSupport) return null;

  const onRequest = async (event: FormEvent) => {
    event.preventDefault();
    if (!reason.trim()) {
      setError("Tell Ordo support what you need help with.");
      return;
    }
    setError(null);
    try {
      await request.mutateAsync(reason.trim());
      setReason("");
      toast({ title: "Ordo support can now come in", description: "The team has been told." });
    } catch (err) {
      const envelope = extractErrorEnvelope(err);
      setError(envelope.errors?.reason?.join(" ") ?? envelope.detail);
    }
  };

  const onWithdraw = async () => {
    const ok = await confirm({
      title: "Withdraw the support request?",
      description: "Ordo support can't come in after this, and a visit in progress ends now.",
      confirmLabel: "Withdraw",
      destructive: true,
    });
    if (!ok) return;
    try {
      await withdraw.mutateAsync();
      toast({ title: "Support request withdrawn" });
    } catch (err) {
      toast({ title: extractErrorEnvelope(err).detail, variant: "destructive" });
    }
  };

  const data = access.data;
  const current = data?.current ?? null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Ordo support</CardTitle>
        <CardDescription>
          The Ordo team can only come into your school after you ask. A request lasts 24 hours.
          Each visit is shown in your audit log with everything done during it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {!data ? (
          <Skeleton className="h-20" />
        ) : current ? (
          <Alert>
            <AlertTitle>Support can come in until {formatTime(current.expires_at)}</AlertTitle>
            <AlertDescription>
              <p>
                Asked by {current.requested_by} on {formatTime(current.created_at)}:
              </p>
              <p className="mt-2 whitespace-pre-wrap">{current.reason}</p>
            </AlertDescription>
          </Alert>
        ) : (
          <form id="support-request" onSubmit={onRequest} className="space-y-2" noValidate>
            <Label htmlFor="support-request-reason">What do you need help with?</Label>
            <textarea
              id="support-request-reason"
              rows={3}
              className={TEXTAREA}
              placeholder="For example: the timetable won't publish for the Science faculty."
              value={reason}
              aria-invalid={error ? true : undefined}
              onChange={(e) => setReason(e.target.value)}
            />
            {error && <p className="text-[13px] text-destructive">{error}</p>}
          </form>
        )}

        {data && data.visits.length > 0 && (
          <div className="space-y-2">
            <p className="font-medium">Recent visits</p>
            <ul className="divide-y divide-border border-y border-border">
              {data.visits.map((visit) => (
                <li key={visit.id} className="flex flex-wrap items-baseline justify-between gap-x-4 py-2">
                  <span>
                    {visit.admin_name}
                    <span className="text-muted-foreground"> · {visit.reason}</span>
                  </span>
                  <span className="font-mono text-[12px] text-muted-foreground">
                    {formatTime(visit.started_at)}
                    {visit.ended_at ? ` to ${formatTime(visit.ended_at)}` : ", still in progress"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
      {data && (
        <CardFooter className="justify-end">
          {current ? (
            <Button variant="outline" onClick={() => void onWithdraw()} disabled={withdraw.isPending}>
              {withdraw.isPending ? "Withdrawing…" : "Withdraw request"}
            </Button>
          ) : (
            <Button type="submit" form="support-request" disabled={request.isPending}>
              {request.isPending ? "Asking…" : "Ask Ordo support in"}
            </Button>
          )}
        </CardFooter>
      )}
    </Card>
  );
}
