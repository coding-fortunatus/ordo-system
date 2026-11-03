import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { MoreHorizontal, UserPlus } from "lucide-react";
import {
  type PlatformAdminRow,
  useInviteAdmin,
  usePlatformAdmins,
  useResendAdminInvite,
  useResetAdmin2fa,
  useSignOutAdmin,
  useUnlockAdmin,
  useUpdateAdmin,
} from "@/api/platform";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
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
import { ACTIONS_HEAD_CLASS, RowActions } from "@/components/data-table/row-actions";
import { extractErrorEnvelope } from "@/lib/api";
import { useConfirm } from "@/lib/confirm";
import {
  PLATFORM_ROLE_LABELS,
  usePlatformAuth,
  type PlatformRole,
} from "@/lib/platform-auth";
import { toast } from "@/lib/use-toast";

function status(row: PlatformAdminRow): { label: string; tone: "secondary" | "outline" | "destructive" } {
  if (!row.is_active) return { label: "Deactivated", tone: "destructive" };
  if (row.invite_pending) return { label: "Invite pending", tone: "outline" };
  if (!row.totp_enrolled) return { label: "2FA not set up", tone: "outline" };
  return { label: "Active", tone: "secondary" };
}

function formatDate(iso: string | null): string {
  if (!iso) return "Never";
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ConsoleAdminsPage() {
  const list = usePlatformAdmins();
  const [inviting, setInviting] = useState(false);

  return (
    <>
      <ListShell
        section="Console · Manage"
        title="Admins"
        description="The Ordo team. Owners can do everything; Support can look after schools but can't create or delete them, change plans or manage admins."
        toolbar={
          <Button variant="brand" onClick={() => setInviting(true)}>
            <UserPlus data-icon="inline-start" />
            Invite admin
          </Button>
        }
        isLoading={list.isLoading}
        error={list.error}
        isEmpty={!list.data?.length}
        emptyMessage="No admins yet."
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Admin</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Last sign in</TableHead>
              <TableHead className={ACTIONS_HEAD_CLASS}>
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.map((row) => {
              const s = status(row);
              return (
                <TableRow key={row.id}>
                  <TableCell>
                    <span className="block font-medium">{row.name}</span>
                    <span className="block text-[12px] text-muted-foreground">{row.email}</span>
                  </TableCell>
                  <TableCell>{PLATFORM_ROLE_LABELS[row.role]}</TableCell>
                  <TableCell>
                    <Badge variant={s.tone}>{s.label}</Badge>
                  </TableCell>
                  <TableCell className="whitespace-nowrap font-mono text-[12px] text-muted-foreground">
                    {formatDate(row.last_login)}
                  </TableCell>
                  <TableCell>
                    <RowActions>
                      <AdminActions row={row} />
                    </RowActions>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </ListShell>
      <InviteAdminDialog open={inviting} onOpenChange={setInviting} />
    </>
  );
}

function AdminActions({ row }: { row: PlatformAdminRow }) {
  const { admin: me } = usePlatformAuth();
  const confirm = useConfirm();
  const update = useUpdateAdmin();
  const reset2fa = useResetAdmin2fa();
  const unlock = useUnlockAdmin();
  const resend = useResendAdminInvite();
  const signOut = useSignOutAdmin();
  const isMe = me?.id === row.id;

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

  const otherRole: PlatformRole = row.role === "owner" ? "support" : "owner";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="grid size-8 place-items-center rounded-md text-muted-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/40"
        aria-label={`Actions for ${row.name}`}
      >
        <MoreHorizontal className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[210px]">
        {row.invite_pending && row.is_active && (
          <DropdownMenuItem
            onClick={() => run(() => resend.mutateAsync(row.id), `New invite sent to ${row.email}`)}
          >
            Resend invite
          </DropdownMenuItem>
        )}
        <DropdownMenuItem
          onClick={() => run(() => unlock.mutateAsync(row.id), `${row.name} can try signing in again`)}
        >
          Unlock sign in
        </DropdownMenuItem>
        {!isMe && (
          <>
            <DropdownMenuItem
              onClick={async () => {
                const ok = await confirm({
                  title: `Make ${row.name} ${PLATFORM_ROLE_LABELS[otherRole]}?`,
                  description:
                    otherRole === "owner"
                      ? "Owners can create and delete schools, change plans and manage admins."
                      : "Support can look after schools but can't create or delete them, change plans or manage admins.",
                  confirmLabel: `Make ${PLATFORM_ROLE_LABELS[otherRole]}`,
                });
                if (ok) {
                  await run(
                    () => update.mutateAsync({ id: row.id, role: otherRole }),
                    `${row.name} is now ${PLATFORM_ROLE_LABELS[otherRole]}`,
                  );
                }
              }}
            >
              Make {PLATFORM_ROLE_LABELS[otherRole]}
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={async () => {
                const ok = await confirm({
                  title: `Sign ${row.name} out everywhere?`,
                  description:
                    "Ends every session they have open, e.g. on a lost laptop. They can sign in again with their password and authenticator.",
                  confirmLabel: "Sign out",
                  destructive: true,
                });
                if (ok) {
                  await run(() => signOut.mutateAsync(row.id), `${row.name} was signed out everywhere`);
                }
              }}
            >
              Sign out everywhere
            </DropdownMenuItem>
            {row.totp_enrolled && (
              <DropdownMenuItem
                onClick={async () => {
                  const ok = await confirm({
                    title: `Reset ${row.name}'s two factor sign in?`,
                    description:
                      "They're signed out everywhere and set up a new authenticator at their next sign in. Use this when they lose their device.",
                    confirmLabel: "Reset",
                    destructive: true,
                  });
                  if (ok) {
                    await run(() => reset2fa.mutateAsync(row.id), "Two factor sign in reset");
                  }
                }}
              >
                Reset two factor sign in
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className={row.is_active ? "text-destructive" : undefined}
              onClick={async () => {
                if (row.is_active) {
                  const ok = await confirm({
                    title: `Deactivate ${row.name}?`,
                    description: "They're signed out at once and can't sign in until reactivated.",
                    confirmLabel: "Deactivate",
                    destructive: true,
                  });
                  if (!ok) return;
                }
                await run(
                  () => update.mutateAsync({ id: row.id, is_active: !row.is_active }),
                  row.is_active ? `${row.name} deactivated` : `${row.name} reactivated`,
                );
              }}
            >
              {row.is_active ? "Deactivate" : "Reactivate"}
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const inviteSchema = z.object({
  name: z.string().trim().min(1, "Name is required"),
  email: z.string().trim().email("Enter a valid email"),
  role: z.enum(["owner", "support"]),
});

type InviteValues = z.infer<typeof inviteSchema>;

function InviteAdminDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const invite = useInviteAdmin();
  const [error, setError] = useState<string | null>(null);
  const form = useForm<InviteValues>({
    resolver: zodResolver(inviteSchema),
    defaultValues: { name: "", email: "", role: "support" },
  });

  const close = (next: boolean) => {
    if (!next) {
      form.reset();
      setError(null);
    }
    onOpenChange(next);
  };

  const onSubmit = async (values: InviteValues) => {
    setError(null);
    try {
      await invite.mutateAsync(values);
      toast({
        title: "Invite sent",
        description: `${values.email} has 48 hours to set a password.`,
      });
      close(false);
    } catch (err) {
      const envelope = extractErrorEnvelope(err);
      const fieldErrors = envelope.errors ?? {};
      for (const field of ["name", "email", "role"] as const) {
        if (fieldErrors[field]) form.setError(field, { message: fieldErrors[field].join(" ") });
      }
      if (!Object.keys(fieldErrors).length) setError(envelope.detail);
    }
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite an admin</DialogTitle>
          <DialogDescription>
            They get an email with a link to set their password. The link works once and expires
            in 48 hours.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input autoFocus {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Email</FormLabel>
                  <FormControl>
                    <Input type="email" {...field} />
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
                  <Select value={field.value} onValueChange={(v) => field.onChange(v as PlatformRole)}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="support">Support</SelectItem>
                      <SelectItem value="owner">Owner</SelectItem>
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => close(false)}>
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
