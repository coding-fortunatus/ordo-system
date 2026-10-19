import { useState } from "react";
import { Plus } from "lucide-react";
import {
  type Course,
  type ExamType,
  useCourses,
  useDeleteCourse,
} from "@/api/courses";
import { useDepartments } from "@/api/departments";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { useAuth } from "@/lib/auth";
import { useConfirm } from "@/lib/confirm";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import { formatTitle } from "@/lib/format";
import { FromPortalBadge } from "@/components/from-portal-badge";
import { CourseFormDialog } from "./course-form-dialog";

export default function CoursesListPage() {
  const { user } = useAuth();
  const isAdmin = !!user?.is_staff;
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [examType, setExamType] = useState<string>("__all__");
  const [department, setDepartment] = useState<string>("__all__");
  const [editing, setEditing] = useState<Course | null>(null);
  const [open, setOpen] = useState(false);

  const list = useCourses({
    page,
    query: query || undefined,
    exam_type: examType === "__all__" ? undefined : (examType as ExamType),
    department:
      isAdmin && department !== "__all__" ? department : undefined,
  });
  const departments = useDepartments({ all: true, enabled: isAdmin });
  const remove = useDeleteCourse();
  const confirm = useConfirm();

  const onDelete = async (course: Course) => {
    const ok = await confirm({
      title: "Delete course?",
      description: `${course.code} will be permanently removed.`,
      confirmLabel: "Delete",
      destructive: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(course.id);
      toast({ title: "Course deleted" });
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
        title="Courses"
        description="Every course that has an exam, across all departments."
        toolbar={
          isAdmin && (
            <Button
              onClick={() => {
                setEditing(null);
                setOpen(true);
              }}
            >
              <Plus data-icon="inline-start" />
              New course
            </Button>
          )
        }
        query={query}
        onQueryChange={(q) => {
          setQuery(q);
          setPage(1);
        }}
        searchPlaceholder="Search by code or title"
        filters={
          <div className="flex flex-wrap items-center gap-2">
            {isAdmin && (
              <Select
                value={department}
                onValueChange={(v) => {
                  setDepartment(v ?? "__all__");
                  setPage(1);
                }}
              >
                <SelectTrigger className="w-[180px]">
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
            )}
            <Select
              value={examType}
              onValueChange={(v) => {
                setExamType(v ?? "__all__");
                setPage(1);
              }}
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue placeholder="All types" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All types</SelectItem>
                <SelectItem value="PBE">PBE</SelectItem>
                <SelectItem value="CBE">CBE</SelectItem>
              </SelectContent>
            </Select>
          </div>
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
              <TableHead className="w-[120px]">Code</TableHead>
              <TableHead>Title</TableHead>
              <TableHead className="w-[120px]">Type</TableHead>
              {isAdmin && <TableHead className={ACTIONS_HEAD_CLASS}>Actions</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.results.map((c) => (
              <TableRow key={c.id}>
                <TableCell className="font-medium">
                  <div className="flex items-center gap-2">
                    {c.code}
                    <FromPortalBadge record={c} />
                  </div>
                </TableCell>
                <TableCell>{formatTitle(c.name)}</TableCell>
                <TableCell>
                  <Badge variant={c.exam_type === "CBE" ? "default" : "secondary"}>
                    {c.exam_type}
                  </Badge>
                </TableCell>
                {isAdmin && (
                  <TableCell className="text-right">
                    <RowActions>
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
      <CourseFormDialog open={open} onOpenChange={setOpen} initial={editing} />
    </>
  );
}
