import { useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { FileUploadCard } from "@/components/file-upload-card";
import {
  useUploadCourses,
  useUploadDepartments,
  useUploadHalls,
} from "@/api/uploads";
import {
  useEnableBulkUpload,
  useSystemSettings,
} from "@/api/system";
import { toast } from "@/lib/use-toast";
import { extractErrorEnvelope } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { isSuperAdmin, useAuth } from "@/lib/auth";
import { Lock } from "lucide-react";

export default function UploadsPage() {
  const { user } = useAuth();
  const settings = useSystemSettings();
  const enable = useEnableBulkUpload();
  const locked = !!settings.data?.has_timetable;
  // Re-enabling invalidates the timetable, so it is restricted to super admins.
  const canUnlock = isSuperAdmin(user);

  const departments = useUploadDepartments();
  const halls = useUploadHalls();
  const courses = useUploadCourses();

  const [departmentsResult, setDepartmentsResult] =
    useState<{ created: number; updated: number } | null>(null);
  const [hallsResult, setHallsResult] =
    useState<{ created: number; updated: number } | null>(null);
  const [coursesResult, setCoursesResult] =
    useState<{ created: number; updated: number } | null>(null);

  const onEnable = async () => {
    try {
      await enable.mutateAsync();
      toast({ title: "Uploads re-enabled" });
    } catch (err) {
      toast({
        title: "Could not unlock",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <div className="space-y-10">
      <PageHeader
        section="Admin · Uploads"
        title="Bulk uploads"
        description="Bring in departments, halls and courses from CSV files. To upload a class's students or courses, open that class."
        meta={
          <span
            className={
              "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.14em] " +
              (locked
                ? "bg-[color:var(--accent-yellow)] text-[color:var(--accent-yellow-fg)]"
                : "bg-[color:var(--accent-green)] text-[color:var(--accent-green-fg)]")
            }
          >
            <Lock className="size-3" strokeWidth={2.25} />
            {locked ? "Sealed · timetable exists" : "Open · accepting uploads"}
          </span>
        }
      />

      {locked && (
        <Alert className="border-[color:var(--accent-yellow-fg)]/20 bg-[color:var(--accent-yellow)]/40">
          <AlertTitle className="font-serif text-[1.125rem]">
            Uploads are locked
          </AlertTitle>
          <AlertDescription className="flex items-center justify-between gap-3">
            <span>
              A timetable already exists for this session. If you turn uploads back on to add new data, the current timetable will no longer be valid.
              {!canUnlock &&
                " A super admin must re-enable uploads before you can continue."}
            </span>
            {canUnlock && (
              <Button
                size="sm"
                variant="outline"
                onClick={onEnable}
                disabled={enable.isPending}
              >
                Re-enable
              </Button>
            )}
          </AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <FileUploadCard
          title="Departments"
          description="CSV columns: Name, Code"
          disabled={locked}
          isPending={departments.isPending}
          result={departmentsResult}
          error={departments.error}
          onUpload={async (file) => {
            const r = await departments.mutateAsync(file);
            setDepartmentsResult(r);
            toast({
              title: "Departments uploaded",
              description: `Created ${r.created}, updated ${r.updated}`,
            });
          }}
        />
        <FileUploadCard
          title="Halls"
          description={
            'CSV columns: EXAM VENUE, CAPACITY, ROWS, COLS. ' +
            'For rows of different lengths, give ROW_SEATS instead of ROWS and COLS ' +
            '(seats per row, front first, quoted: "10,12,12,8") and optionally ALIGN ' +
            '(left, center or right). Optional GROUP (e.g. BE) says which halls stand ' +
            'together; left blank it is taken from the name. Optional SEAT_ORDER: rows ' +
            '(default), rows_rtl, snake, snake_rtl, columns or columns_snake.'
          }
          disabled={locked}
          isPending={halls.isPending}
          result={hallsResult}
          error={halls.error}
          onUpload={async (file) => {
            const r = await halls.mutateAsync(file);
            setHallsResult(r);
            toast({
              title: "Halls uploaded",
              description: `Created ${r.created}, updated ${r.updated}`,
            });
          }}
        />
        <FileUploadCard
          title="Courses"
          description="CSV columns: COURSE CODE, COURSE TITLE, EXAM TYPE (PBE/CBE)"
          disabled={locked}
          isPending={courses.isPending}
          result={coursesResult}
          error={courses.error}
          onUpload={async (file) => {
            const r = await courses.mutateAsync(file);
            setCoursesResult(r);
            toast({
              title: "Courses uploaded",
              description: `Created ${r.created}, updated ${r.updated}`,
            });
          }}
        />
      </div>
    </div>
  );
}
