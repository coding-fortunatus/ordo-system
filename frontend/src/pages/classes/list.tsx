import { useEffect, useMemo, useState } from "react";
import { Plus, Upload } from "lucide-react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  type Class,
  useClasses,
  useCreateClass,
  useDeleteClass,
  useSetClassActive,
  useUpdateClass,
} from "@/api/classes";
import { useDepartments } from "@/api/departments";
import { useSystemSettings } from "@/api/system";
import { useUploadClassesForDepartment } from "@/api/uploads";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
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
import { canManageData, useAuth } from "@/lib/auth";
import { useConfirm } from "@/lib/confirm";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import { EMPTY } from "@/lib/format";
import { FromPortalBadge, PortalLockedHint, portalLocked } from "@/components/from-portal-badge";
import { Link } from "@tanstack/react-router";

export default function ClassesListPage() {
  const { user } = useAuth();
  const isAdmin = !!user?.is_staff || !!user?.department;
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [department, setDepartment] = useState<string>("__all__");
  const [editing, setEditing] = useState<Class | null>(null);
  const [open, setOpen] = useState(false);

  const list = useClasses({
    page,
    query: query || undefined,
    department: department === "__all__" ? undefined : department,
  });
  const remove = useDeleteClass();
  const setActive = useSetClassActive();
  const departments = useDepartments({ all: true, enabled: !!user?.is_staff });
  const settings = useSystemSettings();
  const uploadClasses = useUploadClassesForDepartment();
  // Data managers upload for any department; department officers only their own.
  const isDataManager = canManageData(user);
  const canUploadClasses = isDataManager || (!user?.is_staff && !!user?.department);
  const uploadsLocked = !!settings.data?.has_timetable;
  const [uploadOpen, setUploadOpen] = useState(false);

  const onUploadClasses = async (file: File, deptSlug: string) => {
    try {
      const r = await uploadClasses.mutateAsync({ file, deptSlug });
      toast({
        title: "Classes uploaded",
        description: `Created ${r.created}, updated ${r.updated}`,
      });
      setUploadOpen(false);
    } catch (err) {
      toast({
        title: "Upload failed",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  const confirm = useConfirm();

  const onToggleActive = async (c: Class, is_active: boolean) => {
    try {
      await setActive.mutateAsync({ id: c.id, is_active });
      toast({
        title: is_active ? "Class switched on" : "Class switched off",
        description: is_active
          ? `${c.name} is planned in every generate run again.`
          : `${c.name} is left out of the timetable, distribution and allocation.`,
      });
    } catch (err) {
      toast({
        title: "Could not change the class",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  const onDelete = async (c: Class) => {
    const ok = await confirm({
      title: "Delete class?",
      description: `"${c.name}" will be permanently removed.`,
      confirmLabel: "Delete",
      destructive: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(c.id);
      toast({ title: "Class deleted" });
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
        title="Classes"
        description="Each department's classes, with their courses and students."
        toolbar={
          isAdmin && (
            <div className="flex items-center gap-2">
              {canUploadClasses && (
                <Button
                  variant="outline"
                  onClick={() => setUploadOpen(true)}
                  disabled={uploadClasses.isPending}
                  title={
                    uploadsLocked
                      ? "Uploads are locked because a timetable already exists."
                      : undefined
                  }
                >
                  <Upload data-icon="inline-start" />
                  {uploadClasses.isPending ? "Uploading…" : "Upload CSV"}
                </Button>
              )}
              <Button onClick={() => { setEditing(null); setOpen(true); }}>
                <Plus data-icon="inline-start" /> New class
              </Button>
            </div>
          )
        }
        query={query}
        onQueryChange={(q) => { setQuery(q); setPage(1); }}
        searchPlaceholder="Search by class, department, or course code"
        filters={
          user?.is_staff ? (
            <Select
              value={department}
              onValueChange={(v) => { setDepartment(v ?? "__all__"); setPage(1); }}
            >
              <SelectTrigger>
                <SelectValue placeholder="All departments" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All departments</SelectItem>
                {departments.data?.results.map((d) => (
                  <SelectItem key={d.id} value={d.slug}>
                    {d.slug}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null
        }
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
              <TableHead className="w-[100px]">Dept</TableHead>
              <TableHead>Class</TableHead>
              <TableHead className="w-[140px] text-right">Students</TableHead>
              <TableHead className="w-[90px]">Active</TableHead>
              <TableHead>Courses</TableHead>
              {isAdmin && (
                <TableHead className={ACTIONS_HEAD_CLASS}>Actions</TableHead>
              )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.results.map((c) => (
              <TableRow
                key={c.id}
                className={c.is_active ? undefined : "text-muted-foreground"}
              >
                <TableCell>
                  <kbd className="rounded-[4px] border border-[color:var(--border)] bg-[color:var(--muted)] px-1.5 py-0.5 font-mono text-[10px] tracking-wide">
                    {c.department.slug}
                  </kbd>
                </TableCell>
                <TableCell className="font-serif text-[1.0625rem] tracking-[-0.005em]">
                  <div className="flex items-center gap-2">
                    {c.name}
                    <FromPortalBadge record={c} />
                  </div>
                </TableCell>
                <TableCell className="text-right">
                  <span className="font-mono tabular-nums">
                    {c.student_count.toLocaleString()}
                  </span>
                </TableCell>
                <TableCell>
                  {isAdmin ? (
                    <Switch
                      checked={c.is_active}
                      onCheckedChange={(v) => onToggleActive(c, v)}
                      disabled={setActive.isPending}
                      aria-label={`${c.name} is sitting this session`}
                    />
                  ) : (
                    <span className="font-mono text-[11px]">
                      {c.is_active ? "Yes" : "Off"}
                    </span>
                  )}
                </TableCell>
                <TableCell className="whitespace-normal">
                  <div className="flex flex-wrap gap-1">
                    {c.courses.slice(0, 5).map((co) => (
                      <kbd
                        key={co.id}
                        className="rounded-[4px] border border-[color:var(--border)] bg-[color:var(--muted)] px-1.5 py-0.5 font-mono text-[10px] tracking-wide"
                      >
                        {co.code}
                      </kbd>
                    ))}
                    {c.courses.length > 5 && (
                      <span className="rounded-[4px] px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-muted-foreground">
                        +{c.courses.length - 5}
                      </span>
                    )}
                  </div>
                </TableCell>
                {isAdmin && (
                  <TableCell className="text-right">
                    <RowActions>
                      <Button
                        render={<Link to="/classes/$id" params={{ id: String(c.id) }} />}
                        size="xs"
                        variant="outline"
                      >
                        Manage
                      </Button>
                      <Button
                        size="xs"
                        variant="outline"
                        onClick={() => {
                          setEditing(c);
                          setOpen(true);
                        }}
                      >
                        Edit
                      </Button>
                      {!c.from_portal && (
                        <Button
                          size="xs"
                          variant="destructive"
                          onClick={() => onDelete(c)}
                          disabled={remove.isPending}
                        >
                          Delete
                        </Button>
                      )}
                    </RowActions>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </ListShell>
      <ClassFormDialog open={open} onOpenChange={setOpen} initial={editing} />
      {canUploadClasses && (
        <UploadClassesDialog
          open={uploadOpen}
          onOpenChange={setUploadOpen}
          onUpload={onUploadClasses}
          isPending={uploadClasses.isPending}
          locked={uploadsLocked}
          departments={isDataManager ? (departments.data?.results ?? []) : null}
          ownDeptSlug={user?.department?.slug ?? ""}
        />
      )}
    </>
  );
}

function UploadClassesDialog({
  open,
  onOpenChange,
  onUpload,
  isPending,
  locked,
  departments,
  ownDeptSlug,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onUpload: (file: File, deptSlug: string) => Promise<void>;
  isPending: boolean;
  locked: boolean;
  /** Departments to choose from; null locks the upload to `ownDeptSlug`. */
  departments: { id: number; name: string; slug: string }[] | null;
  ownDeptSlug: string;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [deptSlug, setDeptSlug] = useState("");

  useEffect(() => {
    if (!open) {
      setFile(null);
      setDeptSlug("");
    }
  }, [open]);

  const targetSlug = departments ? deptSlug : ownDeptSlug;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Upload classes CSV</DialogTitle>
          <DialogDescription>
            CSV column: Name. Existing classes are matched by name within the
            department. A Size column is ignored: a class's size is the
            number of students you upload for it.
          </DialogDescription>
        </DialogHeader>
        {locked && (
          <Alert variant="destructive">
            <AlertDescription>
              Uploads are locked because a timetable already exists. Ask an
              administrator to reset the system or re-enable uploads.
            </AlertDescription>
          </Alert>
        )}
        <div className="space-y-3">
          {departments && (
            <Select
              value={deptSlug}
              onValueChange={(v) => setDeptSlug(v ?? "")}
              disabled={locked || isPending}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select department" />
              </SelectTrigger>
              <SelectContent>
                {departments.map((d) => (
                  <SelectItem key={d.id} value={d.slug}>
                    {d.name} ({d.slug})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Input
            type="file"
            accept=".csv,text/csv"
            disabled={locked || isPending}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={() => file && targetSlug && onUpload(file, targetSlug)}
            disabled={!file || !targetSlug || locked || isPending}
          >
            <Upload data-icon="inline-start" />
            {isPending ? "Uploading…" : "Upload"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const schema = z.object({
  name: z.string().trim().min(1, "Class name is required"),
  department_id: z.coerce.number().int().min(1, "Pick a department"),
  visa_code: z.string().trim().max(50).default(""),
});

type Values = z.infer<typeof schema>;

function ClassFormDialog({
  open,
  onOpenChange,
  initial,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  initial: Class | null;
}) {
  const isEdit = !!initial;
  const { user } = useAuth();
  const isStaffAdmin = !!user?.is_staff;
  const userDept = user?.department ?? null;
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", department_id: 0, visa_code: "" },
  });
  const create = useCreateClass();
  const update = useUpdateClass(initial?.id ?? 0);
  const departments = useDepartments({ all: true, enabled: isStaffAdmin });

  const departmentOptions = useMemo(
    () => departments.data?.results ?? [],
    [departments.data],
  );

  useEffect(() => {
    if (!open) return;
    const fallbackDeptId = userDept?.id ?? 0;
    form.reset(
      initial
        ? {
            name: initial.name ?? "",
            department_id: initial.department.id,
            visa_code: initial.visa_code ?? "",
          }
        : { name: "", department_id: fallbackDeptId, visa_code: "" },
    );
    setTopError(null);
  }, [open, initial, form, userDept?.id]);

  const onSubmit = async (v: Values) => {
    setTopError(null);
    try {
      if (isEdit) {
        await update.mutateAsync({
          name: v.name,
          visa_code: v.visa_code,
        });
        toast({ title: "Class updated" });
      } else {
        await create.mutateAsync(v);
        toast({ title: "Class created" });
      }
      onOpenChange(false);
    } catch (err) {
      const env = extractErrorEnvelope(err);
      setTopError(env.detail);
      if (env.errors) {
        for (const [k, msgs] of Object.entries(env.errors)) {
          if (k === "name" || k === "department_id") {
            form.setError(k, { message: msgs.join(", ") });
          }
        }
      }
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit class" : "New class"}</DialogTitle>
          <DialogDescription>
            A class's size is always its uploaded student list. Upload
            students from the class page.
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
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="100 Level"
                      {...field}
                      disabled={portalLocked(initial, "name")}
                    />
                  </FormControl>
                  <PortalLockedHint locked={portalLocked(initial, "name")} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="department_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Department</FormLabel>
                  {isStaffAdmin ? (
                    <Select
                      value={field.value ? String(field.value) : ""}
                      onValueChange={(v) => field.onChange(Number(v))}
                      disabled={isEdit}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Select a department" />
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
                  ) : (
                    <div className="flex h-9 items-center rounded-md border border-[color:var(--border)] bg-[color:var(--muted)] px-3 font-mono text-[12px] text-muted-foreground">
                      {userDept ? `${userDept.slug} · ${userDept.name}` : EMPTY}
                    </div>
                  )}
                  <FormMessage />
                </FormItem>
              )}
            />
            {isEdit && (
              <div className="space-y-1.5">
                <p className="text-sm font-medium">Students</p>
                <div className="flex h-9 items-center rounded-md border border-[color:var(--border)] bg-[color:var(--muted)] px-3 font-mono text-[12px] tabular-nums text-muted-foreground">
                  {initial?.student_count ?? 0} uploaded
                </div>
              </div>
            )}
            <FormField
              control={form.control}
              name="visa_code"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>VISA code (optional)</FormLabel>
                  <FormControl>
                    <Input
                      placeholder={
                        initial?.visa_label
                          ? `Auto: ${initial.visa_label}`
                          : "Auto-derived from name"
                      }
                      {...field}
                    />
                  </FormControl>
                  <p className="text-xs text-muted-foreground">
                    Override the short code used on the VISA document. Leave
                    blank to auto-derive from the class name.
                  </p>
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
