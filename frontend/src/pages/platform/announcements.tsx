import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Info, Megaphone, TriangleAlert } from "lucide-react";
import {
  type AnnouncementRow,
  useAnnouncements,
  useCreateAnnouncement,
  useWithdrawAnnouncement,
} from "@/api/platform";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DatePicker } from "@/components/ui/date-picker";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { extractErrorEnvelope } from "@/lib/api";
import { useConfirm } from "@/lib/confirm";
import { isPlatformOwner, usePlatformAuth } from "@/lib/platform-auth";
import { toast } from "@/lib/use-toast";

const TEXTAREA =
  "flex min-h-[88px] w-full border border-transparent border-b-input bg-transparent px-0 py-2 text-base outline-none transition-[color,border-color] placeholder:text-muted-foreground focus-visible:border-b-ring aria-invalid:border-b-destructive md:text-sm";

const schema = z
  .object({
    message: z.string().trim().min(1, "Write the message.").max(500, "Keep it under 500 characters."),
    level: z.enum(["info", "warning"]),
    // "yyyy-MM-ddTHH:mm" in the admin's own time, or empty.
    starts_at: z.string(),
    ends_at: z.string(),
  })
  .refine((v) => !v.starts_at || !v.ends_at || v.ends_at > v.starts_at, {
    path: ["ends_at"],
    message: "Must be after the start.",
  });
type Values = z.infer<typeof schema>;

const toIso = (local: string) => (local ? new Date(local).toISOString() : undefined);

function formatWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function NewAnnouncement() {
  const create = useCreateAnnouncement();
  const [error, setError] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { message: "", level: "info", starts_at: "", ends_at: "" },
  });

  const onSubmit = async (values: Values) => {
    setError(null);
    try {
      await create.mutateAsync({
        message: values.message,
        level: values.level,
        starts_at: toIso(values.starts_at),
        ends_at: toIso(values.ends_at) ?? null,
      });
      form.reset();
      toast({ title: "Announcement saved", description: "Schools see it from its start time." });
    } catch (err) {
      const envelope = extractErrorEnvelope(err);
      const fields = envelope.errors ?? {};
      for (const f of ["message", "starts_at", "ends_at"] as const) {
        if (fields[f]) form.setError(f, { message: fields[f].join(" ") });
      }
      if (!Object.keys(fields).length) setError(envelope.detail);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>New announcement</CardTitle>
        <CardDescription>
          Shown as a banner at the top of every school's dashboard, e.g. planned maintenance.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error && (
          <Alert variant="destructive" className="mb-4">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="grid gap-4" noValidate>
            <FormField
              control={form.control}
              name="message"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Message</FormLabel>
                  <FormControl>
                    <textarea
                      className={TEXTAREA}
                      placeholder="Ordo will be unavailable on Saturday from 22:00 to 23:00 WAT for maintenance."
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="grid gap-4 sm:grid-cols-3">
              <FormField
                control={form.control}
                name="level"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Style</FormLabel>
                    <Select value={field.value} onValueChange={(v) => field.onChange(v)}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="info">Information</SelectItem>
                        <SelectItem value="warning">Warning</SelectItem>
                      </SelectContent>
                    </Select>
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="starts_at"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Starts</FormLabel>
                    <FormControl>
                      <DatePicker withTime clearable placeholder="Now" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="ends_at"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Ends</FormLabel>
                    <FormControl>
                      <DatePicker
                        withTime
                        clearable
                        placeholder="When withdrawn"
                        min={form.watch("starts_at") || undefined}
                        {...field}
                      />
                    </FormControl>
                    <FormDescription>Times are in your own time zone.</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <div>
              <Button type="submit" variant="brand" disabled={create.isPending}>
                <Megaphone data-icon="inline-start" />
                Announce
              </Button>
            </div>
          </form>
        </Form>
      </CardContent>
    </Card>
  );
}

const STATE_BADGE = {
  showing: { label: "Showing", variant: "default" },
  scheduled: { label: "Scheduled", variant: "warning" },
  ended: { label: "Ended", variant: "secondary" },
} as const;

function AnnouncementItem({ row, canWithdraw }: { row: AnnouncementRow; canWithdraw: boolean }) {
  const withdraw = useWithdrawAnnouncement();
  const confirm = useConfirm();
  const Icon = row.level === "warning" ? TriangleAlert : Info;
  const badge = STATE_BADGE[row.state];
  return (
    <li className="flex items-start gap-4 px-5 py-4">
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-[13.5px] whitespace-pre-line">{row.message}</p>
        <p className="mt-1 font-mono text-[11px] text-muted-foreground">
          {formatWhen(row.starts_at)} → {row.ends_at ? formatWhen(row.ends_at) : "until withdrawn"}
          {row.created_by && ` · ${row.created_by.name}`}
          {row.withdrawn_at && ` · withdrawn ${formatWhen(row.withdrawn_at)}`}
        </p>
      </div>
      <Badge variant={badge.variant}>{badge.label}</Badge>
      {canWithdraw && row.state !== "ended" && (
        <Button
          variant="outline"
          size="sm"
          disabled={withdraw.isPending}
          onClick={async () => {
            const ok = await confirm({
              title: "Withdraw this announcement?",
              description: "Schools stop seeing it straight away.",
              confirmLabel: "Withdraw",
              destructive: true,
            });
            if (ok) {
              await withdraw.mutateAsync(row.id);
              toast({ title: "Announcement withdrawn" });
            }
          }}
        >
          Withdraw
        </Button>
      )}
    </li>
  );
}

/** Messages every school's dashboard shows. Owners write them. */
export default function ConsoleAnnouncementsPage() {
  const { admin } = usePlatformAuth();
  const owner = isPlatformOwner(admin);
  const list = useAnnouncements();

  return (
    <div className="space-y-8">
      <PageHeader
        section="Console · Manage"
        title="Announcements"
        description="A banner every school sees on its dashboard. Up to three show at once, newest first."
      />
      {owner && <NewAnnouncement />}
      <section className="border border-[color:var(--border)] bg-card">
        <div className="flex h-12 items-center border-b border-[color:var(--border)] px-5">
          <h2 className="text-[13.5px] font-medium">Recent announcements</h2>
        </div>
        {list.isLoading ? (
          <Skeleton className="m-5 h-16" />
        ) : !list.data?.length ? (
          <p className="px-5 py-8 text-center text-[13px] text-muted-foreground">Nothing announced yet.</p>
        ) : (
          <ul className="divide-y divide-[color:var(--border)]">
            {list.data.map((row) => (
              <AnnouncementItem key={row.id} row={row} canWithdraw={owner} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
