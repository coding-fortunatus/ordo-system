import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  useAddCourseToClass,
  useClass,
  useSetClassActive,
  useRemoveCourseFromClass,
} from "@/api/classes";
import { useCreateStudent, useStudents } from "@/api/students";
import {
  useUploadClassCourses,
  useUploadClassStudents,
} from "@/api/uploads";
import { useSystemSettings } from "@/api/system";
import { FileUploadCard } from "@/components/file-upload-card";
import { FromPortalBadge, portalLocked } from "@/components/from-portal-badge";
import { PlanUsage } from "@/components/plan-usage";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { canManageData, useAuth } from "@/lib/auth";
import { useConfirm } from "@/lib/confirm";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import { formatEmail, formatName, formatTitle, orEmpty } from "@/lib/format";

export default function ClassDetailPage() {
  const { id } = useParams({ from: "/_app/classes/$id" });
  const numericId = id ? Number(id) : undefined;
  const cls = useClass(numericId);
  const remove = useRemoveCourseFromClass(numericId ?? 0);
  const setActive = useSetClassActive();
  const { user } = useAuth();
  const isAdmin = !!user?.is_staff || !!user?.department;
  // Mirrors the backend upload check: data managers for any department,
  // department officers only for their own.
  const canUpload =
    canManageData(user) ||
    (!!user?.department && user.department.id === cls.data?.department.id);
  const [open, setOpen] = useState(false);
  const confirm = useConfirm();
  // The portal lists this class's courses, so they change there.
  const canEditCourses = isAdmin && !portalLocked(cls.data, "courses");

  const onRemove = async (courseId: number, code: string) => {
    const ok = await confirm({
      title: "Remove course?",
      description: `${code} will be unlinked from this class.`,
      confirmLabel: "Remove",
      destructive: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(courseId);
      toast({ title: "Course removed" });
    } catch (err) {
      toast({
        title: "Remove failed",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <div className="space-y-10">
      <Link
        to="/classes"
        className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3" strokeWidth={2.25} />
        Back to classes
      </Link>

      {cls.isLoading ? (
        <Skeleton className="h-32" />
      ) : cls.data ? (
        <>
          <header className="flex flex-col gap-6 border-b border-[color:var(--border)] pb-8 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                Catalog · Class
              </p>
              <h1 className="mt-3 font-serif text-[2.5rem] leading-[1.05] tracking-[-0.015em] sm:text-[3rem]">
                {cls.data.name}
              </h1>
              <div className="mt-2">
                <FromPortalBadge record={cls.data} />
              </div>
              <p className="mt-2 text-[14.5px] text-muted-foreground">
                {cls.data.department.name}
                <span className="ml-2 font-mono text-[11px] uppercase tracking-[0.12em]">
                  {cls.data.department.slug}
                </span>
              </p>
            </div>
            <div className="flex gap-3">
              <div className="rounded-[10px] border border-[color:var(--border)] bg-card px-5 py-4 text-right">
                <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                  Students enrolled
                </p>
                <p className="mt-1 font-serif text-[2rem] tabular-nums">
                  {cls.data.student_count}
                </p>
              </div>
              <div className="rounded-[10px] border border-[color:var(--border)] bg-card px-5 py-4">
                <p
                  id="class-active-label"
                  className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground"
                >
                  Sitting this session
                </p>
                <div className="mt-3 flex items-center gap-2">
                  {isAdmin ? (
                    <Switch
                      checked={cls.data.is_active}
                      disabled={setActive.isPending}
                      aria-labelledby="class-active-label"
                      onCheckedChange={async (is_active) => {
                        try {
                          await setActive.mutateAsync({
                            id: cls.data!.id,
                            is_active,
                          });
                        } catch (err) {
                          toast({
                            title: "Could not change the class",
                            description: extractErrorEnvelope(err).detail,
                            variant: "destructive",
                          });
                        }
                      }}
                    />
                  ) : null}
                  <span className="text-sm">
                    {cls.data.is_active ? "Yes" : "No, left out of planning"}
                  </span>
                </div>
              </div>
            </div>
          </header>

          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <div>
                <CardTitle className="text-base">Courses</CardTitle>
                <CardDescription>
                  Courses currently linked to this class.
                </CardDescription>
              </div>
              {canEditCourses && numericId !== undefined && (
                <Button onClick={() => setOpen(true)}>
                  <Plus data-icon="inline-start" /> Add course
                </Button>
              )}
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[140px]">Code</TableHead>
                    <TableHead>Title</TableHead>
                    <TableHead className="w-[120px]">Type</TableHead>
                    {canEditCourses && (
                      <TableHead className="w-[80px] text-right" />
                    )}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {cls.data.courses.length === 0 ? (
                    <TableRow>
                      <TableCell
                        colSpan={canEditCourses ? 4 : 3}
                        className="py-12 text-center font-serif italic text-muted-foreground"
                      >
                        No courses assigned yet.
                      </TableCell>
                    </TableRow>
                  ) : (
                    cls.data.courses.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell>
                          <span className="font-mono text-[12px] tracking-wide">
                            {c.code}
                          </span>
                        </TableCell>
                        <TableCell className="font-serif text-[1rem] tracking-[-0.005em]">
                          {formatTitle(c.name)}
                        </TableCell>
                        <TableCell>
                          <span
                            className="rounded-full px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em]"
                            style={
                              c.exam_type === "CBE"
                                ? {
                                    backgroundColor: "var(--accent-blue)",
                                    color: "var(--accent-blue-fg)",
                                  }
                                : {
                                    backgroundColor: "var(--accent-yellow)",
                                    color: "var(--accent-yellow-fg)",
                                  }
                            }
                          >
                            {c.exam_type}
                          </span>
                        </TableCell>
                        {canEditCourses && (
                          <TableCell className="text-right">
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              onClick={() => onRemove(c.id, c.code)}
                              disabled={remove.isPending}
                              title={`Remove ${c.code}`}
                            >
                              <Trash2 className="h-4 w-4" strokeWidth={2} />
                            </Button>
                          </TableCell>
                        )}
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          {numericId !== undefined && (
            <ClassStudentsCard classId={numericId} />
          )}

          {numericId !== undefined && canUpload && (
            <ClassUploadGrid classId={numericId} />
          )}

          {numericId !== undefined && (
            <AddCourseDialog
              open={open}
              onOpenChange={setOpen}
              classId={numericId}
            />
          )}
        </>
      ) : null}
    </div>
  );
}

function ClassStudentsCard({ classId }: { classId: number }) {
  const [page, setPage] = useState(1);
  const [addOpen, setAddOpen] = useState(false);
  const { user } = useAuth();
  const canAdd = !!user?.is_staff || !!user?.department;
  const PAGE_SIZE = 25;
  const list = useStudents({ class: classId, page });
  const total = list.data?.count ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle className="text-base">Students</CardTitle>
          <CardDescription>
            Enrolled students in this class.
          </CardDescription>
        </div>
        {canAdd && (
          <Button onClick={() => setAddOpen(true)}>
            <Plus data-icon="inline-start" /> Add student
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {list.isLoading ? (
          <Skeleton className="h-32" />
        ) : list.error ? (
          <Alert variant="destructive">
            <AlertDescription>
              {extractErrorEnvelope(list.error).detail}
            </AlertDescription>
          </Alert>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[180px]">Matric no.</TableHead>
                  <TableHead>Student</TableHead>
                  <TableHead className="w-[260px]">Email</TableHead>
                  <TableHead className="w-[140px]">Phone</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {!list.data?.results.length ? (
                  <TableRow>
                    <TableCell
                      colSpan={4}
                      className="py-12 text-center font-serif italic text-muted-foreground"
                    >
                      No students enrolled yet.
                    </TableCell>
                  </TableRow>
                ) : (
                  list.data.results.map((s) => (
                    <TableRow key={s.id}>
                      <TableCell>
                        <span className="font-mono text-[12px] tracking-wide">
                          {orEmpty(s.matric_no)}
                        </span>
                      </TableCell>
                      <TableCell className="font-serif text-[1rem] tracking-[-0.005em]">
                        <div className="flex items-center gap-2">
                          {formatName(s.first_name, s.last_name)}
                          <FromPortalBadge record={s} />
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {formatEmail(s.email)}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {orEmpty(s.phone)}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
            {pageCount > 1 && (
              <div className="mt-4 flex items-center justify-between">
                <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                  Page {page} of {pageCount} · {total} students
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page <= 1}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                  >
                    Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page >= pageCount}
                    onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </CardContent>
      <AddStudentDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        classId={classId}
      />
    </Card>
  );
}

const studentSchema = z.object({
  first_name: z.string().trim().min(1, "First name is required"),
  last_name: z.string().trim().min(1, "Last name is required"),
  matric_no: z.string().trim().min(1, "Matric number is required"),
  email: z.string().trim().email("Invalid email").or(z.literal("")),
  phone: z.string().trim().optional().default(""),
});
type StudentValues = z.infer<typeof studentSchema>;

function AddStudentDialog({
  open,
  onOpenChange,
  classId,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  classId: number;
}) {
  const create = useCreateStudent();
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<StudentValues>({
    resolver: zodResolver(studentSchema),
    defaultValues: {
      first_name: "",
      last_name: "",
      matric_no: "",
      email: "",
      phone: "",
    },
  });

  const onSubmit = async (v: StudentValues) => {
    setTopError(null);
    try {
      await create.mutateAsync({
        first_name: v.first_name,
        last_name: v.last_name,
        matric_no: v.matric_no,
        email: v.email,
        phone: v.phone ?? "",
        class_id: classId,
      });
      toast({ title: "Student added" });
      form.reset();
      onOpenChange(false);
    } catch (err) {
      const env = extractErrorEnvelope(err);
      setTopError(env.detail);
      if (env.errors) {
        for (const [k, msgs] of Object.entries(env.errors)) {
          if (
            ["first_name", "last_name", "matric_no", "email", "phone"].includes(
              k,
            )
          ) {
            form.setError(k as keyof StudentValues, {
              message: msgs.join(", "),
            });
          }
        }
      }
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add student</DialogTitle>
          <DialogDescription>
            The student will be enrolled in this class.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-3">
            {topError && (
              <Alert variant="destructive">
                <AlertDescription>{topError}</AlertDescription>
              </Alert>
            )}
            <div className="grid grid-cols-2 gap-3">
              <FormField
                control={form.control}
                name="first_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>First name</FormLabel>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
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
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <FormField
              control={form.control}
              name="matric_no"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Matric number</FormLabel>
                  <FormControl>
                    <Input {...field} />
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
              name="phone"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Phone</FormLabel>
                  <FormControl>
                    <Input {...field} />
                  </FormControl>
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
                {form.formState.isSubmitting ? "Saving…" : "Add student"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

function ClassUploadGrid({ classId }: { classId: number }) {
  const settings = useSystemSettings();
  const locked = !!settings.data?.has_timetable;
  const courseUpload = useUploadClassCourses(classId);
  const studentUpload = useUploadClassStudents(classId);
  const [coursesResult, setCoursesResult] =
    useState<{ created: number; updated: number } | null>(null);
  const [studentsResult, setStudentsResult] =
    useState<{ created: number; updated: number } | null>(null);

  return (
    <div className="space-y-3">
      <PlanUsage kind="students" className="block text-right" />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <FileUploadCard
          title="Upload class courses"
          description="CSV with a single COURSE CODE column. Courses must already exist in the catalog."
          disabled={locked}
          isPending={courseUpload.isPending}
          result={coursesResult}
          error={courseUpload.error}
          onUpload={async (file) => {
            const r = await courseUpload.mutateAsync(file);
            setCoursesResult(r);
            toast({
              title: "Courses linked",
              description: `Added ${r.created}, already-linked ${r.updated}`,
            });
          }}
        />
        <FileUploadCard
          title="Upload class students"
          description="CSV columns: MATRIC NUMBER, FIRSTNAME, LASTNAME, EMAIL, PHONE NUMBER"
          disabled={locked}
          isPending={studentUpload.isPending}
          result={studentsResult}
          error={studentUpload.error}
          onUpload={async (file) => {
            const r = await studentUpload.mutateAsync(file);
            setStudentsResult(r);
            toast({
              title: "Students uploaded",
              description: `Created ${r.created}, updated ${r.updated}`,
            });
          }}
        />
      </div>
    </div>
  );
}

const schema = z.object({
  name: z.string().trim().min(1, "Title is required"),
  code: z.string().trim().min(1, "Code is required"),
  exam_type: z.enum(["PBE", "CBE"]),
});
type Values = z.infer<typeof schema>;

function AddCourseDialog({
  open,
  onOpenChange,
  classId,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  classId: number;
}) {
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", code: "", exam_type: "PBE" },
  });
  const add = useAddCourseToClass(classId);

  const onSubmit = async (v: Values) => {
    setTopError(null);
    try {
      await add.mutateAsync({
        name: v.name.trim(),
        code: v.code.trim().toUpperCase(),
        exam_type: v.exam_type,
      });
      toast({ title: "Course added" });
      onOpenChange(false);
      form.reset();
    } catch (err) {
      const env = extractErrorEnvelope(err);
      setTopError(env.detail);
      if (env.errors) {
        for (const [k, msgs] of Object.entries(env.errors)) {
          if (k === "name" || k === "code") {
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
          <DialogTitle>Add course to class</DialogTitle>
          <DialogDescription>
            If a course with this code already exists, it will be linked here.
            Otherwise a new course is created.
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
              name="code"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Code</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      onChange={(e) =>
                        field.onChange(e.target.value.toUpperCase())
                      }
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Title</FormLabel>
                  <FormControl><Input {...field} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="exam_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Exam type</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="PBE">PBE</SelectItem>
                      <SelectItem value="CBE">CBE</SelectItem>
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
                {form.formState.isSubmitting ? "Adding…" : "Add"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
