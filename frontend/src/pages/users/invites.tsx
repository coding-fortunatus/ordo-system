import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { MailPlus } from "lucide-react";
import {
  type InviteRow,
  useCreateInvite,
  useInvites,
  useResendInvite,
  useRevokeInvite,
} from "@/api/invites";
import { useDepartments } from "@/api/departments";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { PlanUsage } from "@/components/plan-usage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
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
import { ListShell } from "@/components/data-table/list-shell";
import { PaginationFooter } from "@/components/data-table/pagination";
import { ACTIONS_HEAD_CLASS, RowActions } from "@/components/data-table/row-actions";
import { extractErrorEnvelope } from "@/lib/api";
import { ROLE_LABELS, type UserRole } from "@/lib/auth";
import { useConfirm } from "@/lib/confirm";
import { toast } from "@/lib/use-toast";

function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
  });
}

/** Open invites: who's been asked to join and hasn't yet. */
export function InvitesList({ tabs, onInvite }: { tabs: ReactNode; onInvite: () => void }) {
  const [page, setPage] = useState(1);
  const list = useInvites({ page });
  const revoke = useRevokeInvite();
  const resend = useResendInvite();
  const confirm = useConfirm();

  const run = async (action: () => Promise<unknown>, done: string) => {
    try {
      await action();
      toast({ title: done });
    } catch (err) {
      toast({
        title: "That didn't work",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  const onRevoke = async (invite: InviteRow) => {
    const ok = await confirm({
      title: "Revoke this invite?",
      description: `The link sent to ${invite.email} stops working at once.`,
      confirmLabel: "Revoke",
      destructive: true,
    });
    if (ok) await run(() => revoke.mutateAsync(invite.id), "Invite revoked");
  };

  return (
    <ListShell
      section="Admin · Users"
      title="Users"
      description="People you've invited who haven't joined yet. Each link lasts 7 days; resend one to start a fresh 7 days."
      toolbar={
        <Button variant="brand" onClick={onInvite}>
          <MailPlus data-icon="inline-start" /> Invite staff
        </Button>
      }
      filters={
        <>
          {tabs}
          <PlanUsage kind="staff_users" />
        </>
      }
      isLoading={list.isLoading}
      error={list.error}
      isEmpty={!list.data?.results.length}
      emptyMessage="No open invites. Invite staff to send someone a link to join."
      pagination={
        list.data && (
          <PaginationFooter page={page} pageSize={15} total={list.data.count} onPageChange={setPage} />
        )
      }
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Email</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Invited by</TableHead>
            <TableHead>Link</TableHead>
            <TableHead className={ACTIONS_HEAD_CLASS}>Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {list.data?.results.map((invite) => (
            <TableRow key={invite.id}>
              <TableCell className="font-medium">{invite.email}</TableCell>
              <TableCell>
                <Badge variant={invite.role ? "default" : "secondary"}>
                  {invite.role ? ROLE_LABELS[invite.role] : "Department Officer"}
                </Badge>
                {invite.department && (
                  <span className="mt-0.5 block font-mono text-[11px] text-muted-foreground">
                    {invite.department.slug}
                  </span>
                )}
              </TableCell>
              <TableCell>{invite.invited_by?.name ?? "Ordo"}</TableCell>
              <TableCell className="whitespace-nowrap">
                {invite.is_expired ? (
                  <Badge variant="warning">Expired</Badge>
                ) : (
                  <span className="font-mono text-[12px] text-muted-foreground">
                    Until {formatDay(invite.expires_at)}
                  </span>
                )}
              </TableCell>
              <TableCell className="text-right">
                <RowActions>
                  <Button
                    size="xs"
                    variant="outline"
                    disabled={resend.isPending}
                    onClick={() =>
                      run(() => resend.mutateAsync(invite.id), `New link sent to ${invite.email}`)
                    }
                  >
                    Resend
                  </Button>
                  <Button
                    size="xs"
                    variant="destructive"
                    disabled={revoke.isPending}
                    onClick={() => onRevoke(invite)}
                  >
                    Revoke
                  </Button>
                </RowActions>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </ListShell>
  );
}

// "DEPT" is the form sentinel for a department officer (role = null on the API).
const DEPT_OFFICER = "DEPT";

const ROLE_OPTIONS: { value: UserRole | typeof DEPT_OFFICER; label: string }[] = [
  { value: "SA", label: ROLE_LABELS.SA },
  { value: "DO", label: ROLE_LABELS.DO },
  { value: "FO", label: ROLE_LABELS.FO },
  { value: "ECM", label: ROLE_LABELS.ECM },
  { value: DEPT_OFFICER, label: "Department Officer (department scoped)" },
];

const inviteSchema = z
  .object({
    email: z.string().trim().email("Enter a valid email"),
    role: z.enum(["SA", "DO", "FO", "ECM", DEPT_OFFICER]),
    department_id: z.number().int().nullable(),
  })
  .refine((v) => v.role !== DEPT_OFFICER || v.department_id !== null, {
    path: ["department_id"],
    message: "Pick the department this officer looks after",
  });

type InviteValues = z.infer<typeof inviteSchema>;

export function InviteStaffDialog({
  open,
  onOpenChange,
  onSent,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSent?: () => void;
}) {
  const create = useCreateInvite();
  const departments = useDepartments({ all: true });
  const departmentOptions = useMemo(() => departments.data?.results ?? [], [departments.data]);
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<InviteValues>({
    resolver: zodResolver(inviteSchema),
    defaultValues: { email: "", role: DEPT_OFFICER, department_id: null },
  });

  useEffect(() => {
    if (!open) return;
    form.reset({ email: "", role: DEPT_OFFICER, department_id: null });
    setTopError(null);
  }, [open, form]);

  const onSubmit = async (values: InviteValues) => {
    setTopError(null);
    const role = values.role === DEPT_OFFICER ? null : values.role;
    try {
      await create.mutateAsync({
        email: values.email,
        role,
        department_id: role ? null : values.department_id,
      });
      toast({
        title: "Invite sent",
        description: `${values.email} has 7 days to join.`,
      });
      onOpenChange(false);
      onSent?.();
    } catch (err) {
      const envelope = extractErrorEnvelope(err);
      const errors = envelope.errors ?? {};
      for (const field of ["email", "role", "department_id"] as const) {
        if (errors[field]) form.setError(field, { message: errors[field].join(" ") });
      }
      if (!Object.keys(errors).length) setTopError(envelope.detail);
    }
  };

  const isDeptOfficer = form.watch("role") === DEPT_OFFICER;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite staff</DialogTitle>
          <DialogDescription>
            They get an email with a link to choose their name and password. Nobody else ever
            handles their password. The link lasts 7 days.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            {topError && (
              <Alert variant="destructive">
                <AlertDescription>{topError}</AlertDescription>
              </Alert>
            )}
            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Email</FormLabel>
                  <FormControl>
                    <Input type="email" autoFocus {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="role"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Role</FormLabel>
                  <Select value={field.value} onValueChange={(v) => field.onChange(v)}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {ROLE_OPTIONS.map((opt) => (
                        <SelectItem key={opt.value} value={opt.value}>
                          {opt.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
            {isDeptOfficer && (
              <FormField
                control={form.control}
                name="department_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Department</FormLabel>
                    <Select
                      value={field.value ? String(field.value) : ""}
                      onValueChange={(v) => field.onChange(v ? Number(v) : null)}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Pick a department" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {departmentOptions.map((d) => (
                          <SelectItem key={d.id} value={String(d.id)}>
                            {d.slug} · {d.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" variant="brand" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting ? "Sending…" : "Send invite"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
