import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Download, MailPlus, Plus, Sparkles } from "lucide-react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  type UserRow,
  useChangeUserPassword,
  useCreateUser,
  useDeleteUser,
  useSeedDepartmentUsers,
  useUpdateUser,
  useUsers,
} from "@/api/users";
import { useDepartments } from "@/api/departments";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { PlanUsage } from "@/components/plan-usage";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
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
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { ROLE_LABELS, useAuth, type UserRole } from "@/lib/auth";
import { useConfirm } from "@/lib/confirm";
import { extractErrorEnvelope } from "@/lib/api";
import { downloadAuthenticatedFile } from "@/lib/download";
import { toast } from "@/lib/use-toast";
import { formatEmail, formatName, orEmpty } from "@/lib/format";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { InviteStaffDialog, InvitesList } from "./invites";

type UsersTab = "users" | "invites";

/** Users and Invites share the page; the tab lives in the URL. */
export default function UsersListPage() {
  const { tab } = useSearch({ from: "/_app/users" });
  const navigate = useNavigate({ from: "/users" });
  const current: UsersTab = tab ?? "users";
  const [inviting, setInviting] = useState(false);
  const show = (next: UsersTab) =>
    navigate({ search: next === "invites" ? { tab: "invites" } : {}, replace: true });

  const tabs = (
    <Tabs value={current} onValueChange={(v) => show(v as UsersTab)}>
      <TabsList variant="line">
        <TabsTrigger value="users">Users</TabsTrigger>
        <TabsTrigger value="invites">Invites</TabsTrigger>
      </TabsList>
    </Tabs>
  );

  return (
    <>
      {current === "invites" ? (
        <InvitesList tabs={tabs} onInvite={() => setInviting(true)} />
      ) : (
        <UsersTable tabs={tabs} onInvite={() => setInviting(true)} />
      )}
      <InviteStaffDialog
        open={inviting}
        onOpenChange={setInviting}
        onSent={() => show("invites")}
      />
    </>
  );
}

function UsersTable({ tabs, onInvite }: { tabs: ReactNode; onInvite: () => void }) {
  const { user: me } = useAuth();
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<UserRow | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [pwUser, setPwUser] = useState<UserRow | null>(null);
  const [pwOpen, setPwOpen] = useState(false);
  const [seedOpen, setSeedOpen] = useState(false);

  const list = useUsers({ page, query: query || undefined });
  const remove = useDeleteUser();
  const confirm = useConfirm();
  const [exporting, setExporting] = useState(false);

  const onExport = async () => {
    setExporting(true);
    try {
      await downloadAuthenticatedFile(
        "/users/export/",
        "departmental-staff.csv",
        query ? { query } : undefined,
      );
    } catch (err) {
      toast({
        title: "Export failed",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    } finally {
      setExporting(false);
    }
  };

  const onDelete = async (u: UserRow) => {
    const ok = await confirm({
      title: "Delete user?",
      description: `${u.email} will lose access immediately.`,
      confirmLabel: "Delete",
      destructive: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(u.id);
      toast({ title: "User deleted" });
    } catch (err) {
      toast({
        title: "Delete failed",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <>
      <ListShell
        section="Admin · Users"
        title="Users"
        emptyMessage={query ? "No users match your search." : "No users yet."}
        description="Everyone who can sign in to Ordo, from admins to department staff."
        toolbar={
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={onExport}
              disabled={exporting}
            >
              <Download data-icon="inline-start" />
              {exporting ? "Exporting…" : "Export CSV"}
            </Button>
            <Button variant="outline" onClick={() => setSeedOpen(true)}>
              <Sparkles data-icon="inline-start" /> Seed dept users
            </Button>
            <Button variant="outline" onClick={() => { setEditing(null); setEditOpen(true); }}>
              <Plus data-icon="inline-start" /> New user
            </Button>
            <Button variant="brand" onClick={onInvite}>
              <MailPlus data-icon="inline-start" /> Invite staff
            </Button>
          </div>
        }
        filters={
          <>
            {tabs}
            <PlanUsage kind="staff_users" />
          </>
        }
        query={query}
        onQueryChange={(q) => { setQuery(q); setPage(1); }}
        searchPlaceholder="Search by name or email"
        isLoading={list.isLoading}
        error={list.error}
        isEmpty={!list.data?.results.length}
        pagination={
          list.data && (
            <PaginationFooter
              page={page}
              pageSize={15}
              total={list.data.count}
              onPageChange={setPage}
            />
          )
        }
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Email</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Department</TableHead>
              <TableHead>Role</TableHead>
              <TableHead className={ACTIONS_HEAD_CLASS}>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.results.map((u) => (
              <TableRow key={u.id}>
                <TableCell className="font-medium">{formatEmail(u.email)}</TableCell>
                <TableCell>{formatName(u.full_name)}</TableCell>
                <TableCell>{orEmpty(u.department?.slug)}</TableCell>
                <TableCell>
                  <Badge variant={u.role ? "default" : "secondary"}>
                    {u.role ? ROLE_LABELS[u.role] : "Department Officer"}
                  </Badge>
                </TableCell>
                <TableCell className="text-right">
                  <RowActions>
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => { setEditing(u); setEditOpen(true); }}
                    >
                      Edit
                    </Button>
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => { setPwUser(u); setPwOpen(true); }}
                    >
                      Password
                    </Button>
                    <Button
                      size="xs"
                      variant="destructive"
                      onClick={() => onDelete(u)}
                      disabled={remove.isPending || u.id === me?.id}
                    >
                      Delete
                    </Button>
                  </RowActions>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </ListShell>
      <UserFormDialog open={editOpen} onOpenChange={setEditOpen} initial={editing} />
      <ChangePasswordDialog open={pwOpen} onOpenChange={setPwOpen} user={pwUser} />
      <SeedDepartmentUsersDialog open={seedOpen} onOpenChange={setSeedOpen} />
    </>
  );
}

// "DEPT" is the form sentinel for a department officer (role = null on the API).
const DEPT_OFFICER = "DEPT";

const ROLE_OPTIONS: { value: UserRole | typeof DEPT_OFFICER; label: string }[] = [
  { value: "SA", label: ROLE_LABELS.SA },
  { value: "DO", label: ROLE_LABELS.DO },
  { value: "FO", label: ROLE_LABELS.FO },
  { value: "ECM", label: ROLE_LABELS.ECM },
  { value: DEPT_OFFICER, label: "Department Officer (department-scoped)" },
];

const userSchema = z.object({
  email: z.string().email("Valid email required"),
  first_name: z.string().trim().min(1),
  last_name: z.string().trim().min(1),
  role: z.enum(["SA", "DO", "FO", "ECM", DEPT_OFFICER]),
  department_id: z.coerce.number().int().nullable(),
  password: z.string().min(8, "At least 8 characters").or(z.literal("")),
});

type UserValues = z.infer<typeof userSchema>;

function UserFormDialog({
  open,
  onOpenChange,
  initial,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  initial: UserRow | null;
}) {
  const isEdit = !!initial;
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<UserValues>({
    resolver: zodResolver(userSchema),
    defaultValues: {
      email: "",
      first_name: "",
      last_name: "",
      role: DEPT_OFFICER,
      department_id: null,
      password: "",
    },
  });
  const create = useCreateUser();
  const update = useUpdateUser(initial?.id ?? 0);
  const departments = useDepartments({ all: true });
  const departmentOptions = useMemo(
    () => departments.data?.results ?? [],
    [departments.data],
  );

  useEffect(() => {
    if (!open) return;
    form.reset(
      initial
        ? {
            email: initial.email,
            first_name: initial.first_name,
            last_name: initial.last_name,
            role: initial.role ?? DEPT_OFFICER,
            department_id: initial.department?.id ?? null,
            password: "",
          }
        : {
            email: "",
            first_name: "",
            last_name: "",
            role: DEPT_OFFICER,
            department_id: null,
            password: "",
          },
    );
    setTopError(null);
  }, [open, initial, form]);

  const onSubmit = async (v: UserValues) => {
    setTopError(null);
    const role = v.role === DEPT_OFFICER ? null : v.role;
    const payload: Record<string, unknown> = {
      email: v.email.trim(),
      first_name: v.first_name.trim(),
      last_name: v.last_name.trim(),
      role,
      // Admin-side roles aren't department-scoped; only officers carry a department.
      department_id: role ? null : v.department_id,
    };
    if (v.password) payload.password = v.password;
    try {
      if (isEdit) {
        await update.mutateAsync(payload);
        toast({ title: "User updated" });
      } else {
        if (!v.password) {
          form.setError("password", { message: "Password is required" });
          return;
        }
        await create.mutateAsync(payload as never);
        toast({ title: "User created" });
      }
      onOpenChange(false);
    } catch (err) {
      const env = extractErrorEnvelope(err);
      setTopError(env.detail);
      if (env.errors) {
        for (const [k, msgs] of Object.entries(env.errors)) {
          if (
            ["email", "first_name", "last_name", "password", "department_id", "role"].includes(k)
          ) {
            form.setError(k as keyof UserValues, { message: msgs.join(", ") });
          }
        }
      }
    }
  };

  const isDeptOfficer = form.watch("role") === DEPT_OFFICER;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit user" : "New user"}</DialogTitle>
          <DialogDescription>
            Assign an admin-side role (Super Admin, Data Officer, Faculty
            Officer, or Exam Committee Member), or make a department officer
            scoped to a single department.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-3">
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
                  <FormControl><Input type="email" {...field} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="grid grid-cols-2 gap-3">
              <FormField
                control={form.control}
                name="first_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>First name</FormLabel>
                    <FormControl><Input {...field} /></FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="last_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Last name</FormLabel>
                    <FormControl><Input {...field} /></FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <FormField
              control={form.control}
              name="role"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Role</FormLabel>
                  <Select
                    value={field.value}
                    onValueChange={(v) => field.onChange(v)}
                  >
                    <FormControl>
                      <SelectTrigger><SelectValue /></SelectTrigger>
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
                      onValueChange={(v) =>
                        field.onChange(v === "" ? null : Number(v))
                      }
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
            <FormField
              control={form.control}
              name="password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {isEdit ? "Password (leave blank to keep current)" : "Password"}
                  </FormLabel>
                  <FormControl><PasswordInput {...field} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting
                  ? "Saving…"
                  : isEdit
                    ? "Save changes"
                    : "Create"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

const seedSchema = z.object({
  password: z.string().min(8, "At least 8 characters"),
  overwrite: z.boolean(),
});

type SeedValues = z.infer<typeof seedSchema>;

function SeedDepartmentUsersDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<SeedValues>({
    resolver: zodResolver(seedSchema),
    defaultValues: { password: "Ems@2025", overwrite: false },
  });
  const seed = useSeedDepartmentUsers();

  useEffect(() => {
    if (open) {
      form.reset({ password: "Ems@2025", overwrite: false });
      setTopError(null);
    }
  }, [open, form]);

  const onSubmit = async (v: SeedValues) => {
    setTopError(null);
    try {
      const res = await seed.mutateAsync(v);
      toast({
        title: "Department users seeded",
        description: res.detail,
      });
      onOpenChange(false);
    } catch (err) {
      setTopError(extractErrorEnvelope(err).detail);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Seed department users</DialogTitle>
          <DialogDescription>
            Creates one staff account per department using
            <code className="mx-1 rounded bg-muted px-1 py-0.5 text-xs">
              {"{slug}@ems.com"}
            </code>
            with the password below. Use this once to bootstrap login access
            for every department.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-3">
            {topError && (
              <Alert variant="destructive">
                <AlertDescription>{topError}</AlertDescription>
              </Alert>
            )}
            <FormField
              control={form.control}
              name="password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Shared password</FormLabel>
                  <FormControl><Input {...field} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="overwrite"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>If a seeded email already exists</FormLabel>
                  <Select
                    value={field.value ? "overwrite" : "skip"}
                    onValueChange={(v) => field.onChange(v === "overwrite")}
                  >
                    <FormControl>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="skip">Skip (keep existing)</SelectItem>
                      <SelectItem value="overwrite">
                        Overwrite (reset password + role)
                      </SelectItem>
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting ? "Seeding…" : "Seed users"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

const pwSchema = z
  .object({
    password: z.string().min(8, "At least 8 characters"),
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, {
    message: "Passwords do not match",
    path: ["confirm"],
  });

type PwValues = z.infer<typeof pwSchema>;

function ChangePasswordDialog({
  open,
  onOpenChange,
  user,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  user: UserRow | null;
}) {
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<PwValues>({
    resolver: zodResolver(pwSchema),
    defaultValues: { password: "", confirm: "" },
  });
  const change = useChangeUserPassword(user?.id ?? 0);

  useEffect(() => {
    if (open) {
      form.reset({ password: "", confirm: "" });
      setTopError(null);
    }
  }, [open, form]);

  const onSubmit = async (v: PwValues) => {
    setTopError(null);
    try {
      await change.mutateAsync(v.password);
      toast({ title: "Password changed" });
      onOpenChange(false);
    } catch (err) {
      setTopError(extractErrorEnvelope(err).detail);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Change password</DialogTitle>
          <DialogDescription>
            Set a new password for {user?.email}. Their existing API token will be
            invalidated.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-3">
            {topError && (
              <Alert variant="destructive">
                <AlertDescription>{topError}</AlertDescription>
              </Alert>
            )}
            <FormField
              control={form.control}
              name="password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>New password</FormLabel>
                  <FormControl><PasswordInput {...field} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="confirm"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Confirm</FormLabel>
                  <FormControl><PasswordInput {...field} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting ? "Saving…" : "Change password"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
