import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { useTimetableDates } from "@/api/scheduling";
import { useHalls } from "@/api/halls";
import { useHasFeature } from "@/api/school";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { downloadAuthenticatedFile } from "@/lib/download";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import { PageHeader } from "@/components/layout/page-header";

export default function ExportsPage() {
  const { user } = useAuth();
  const isAdmin = !!user?.is_staff;
  // Seating documents need seat allocation; the broadsheet and the all halls
  // bundle need exports (spec 0003, child 0005).
  const seating = useHasFeature("seat_allocation");
  const exports = useHasFeature("exports");
  const distribution = useHasFeature("distribution");
  const dates = useTimetableDates();
  const halls = useHalls({ all: true });
  // Whether a timetable exists. Not ``has_timetable``: that flag is the
  // upload lock, and "Enable bulk upload" clears it with the timetable kept.
  const hasTimetable = (dates.data?.dates.length ?? 0) > 0;
  const [date, setDate] = useState<string | undefined>();
  const [period, setPeriod] = useState<"AM" | "PM">("AM");
  const [hallId, setHallId] = useState<string | undefined>();

  useEffect(() => {
    if (!date && dates.data?.dates.length) setDate(dates.data.dates[0]);
  }, [date, dates.data]);

  const tryDownload = async (url: string, fallback: string, params?: Record<string, string | number | undefined>) => {
    try {
      await downloadAuthenticatedFile(url, fallback, params);
      toast({ title: "Download started" });
    } catch (err) {
      toast({
        title: "Download failed",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <div className="space-y-10">
      <PageHeader
        section="Operations · Exports"
        title="Exports"
        description="Download reports as CSV, Excel or Word. Anything tied to one exam uses the date and period you pick below."
      />

      {isAdmin && (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Slot</CardTitle>
          <CardDescription>
            Pick the exam date and period for the distribution, arrangement and attendance sheet downloads.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-2">
          <Select
            value={date ?? ""}
            onValueChange={(v) => setDate(v ?? undefined)}
            disabled={!dates.data?.dates.length}
          >
            <SelectTrigger>
              <SelectValue placeholder="Pick a date" />
            </SelectTrigger>
            <SelectContent>
              {dates.data?.dates.map((d) => (
                <SelectItem key={d} value={d}>
                  {d}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={period}
            onValueChange={(v) => setPeriod(v as "AM" | "PM")}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="AM">AM</SelectItem>
              <SelectItem value="PM">PM</SelectItem>
            </SelectContent>
          </Select>
          {seating && (
            <Select value={hallId ?? ""} onValueChange={(v) => setHallId(v ?? undefined)}>
              <SelectTrigger>
                <SelectValue placeholder="Hall (for attendance sheets)" />
              </SelectTrigger>
              <SelectContent>
                {halls.data?.results.map((h) => (
                  <SelectItem key={h.id} value={String(h.id)}>
                    {h.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </CardContent>
      </Card>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        <ExportCard
          title="Timetable CSV"
          description={
            isAdmin
              ? "Full exam schedule across every department."
              : "Your department's exam schedule as CSV."
          }
          disabled={!hasTimetable}
          disabledHint={!hasTimetable ? "No timetable has been generated yet." : undefined}
          onDownload={() =>
            tryDownload("/exports/timetable/", "timetable.csv")
          }
        />
        {isAdmin && (
          <>
            {distribution && (
            <ExportCard
              title="Distribution CSV"
              description="Hall-to-class assignments for the selected slot."
              disabled={!hasTimetable || !date || !period}
              disabledHint={!hasTimetable ? "No timetable has been generated yet." : undefined}
              onDownload={() =>
                tryDownload(
                  "/exports/distribution/",
                  `distribution-${date}-${period}.csv`,
                  { date, period },
                )
              }
            />
            )}
            {seating && (
            <ExportCard
              title="Arrangement ZIP"
              description="Attendance sheets for the selected slot grouped by course (DOCX zip)."
              disabled={!hasTimetable || !date || !period}
              disabledHint={!hasTimetable ? "No timetable has been generated yet." : undefined}
              onDownload={() =>
                tryDownload(
                  "/exports/arrangement/",
                  `arrangement-${date}-${period}.zip`,
                  { date, period },
                )
              }
            />
            )}
            {seating && exports && (
            <ExportCard
              title="Attendance sheets · all halls"
              description="Every hall's attendance sheets for the selected slot, one folder per hall (DOCX zip)."
              disabled={!hasTimetable || !date || !period}
              disabledHint={!hasTimetable ? "No timetable has been generated yet." : undefined}
              onDownload={() =>
                tryDownload(
                  "/exports/attendance-sheets/bulk/",
                  `attendance-all-halls-${date}-${period}.zip`,
                  { date, period },
                )
              }
            />
            )}
            {seating && (
            <ExportCard
              title="Attendance sheets · one hall"
              description="The selected hall's sheets, one per course and class (DOCX zip)."
              disabled={!hasTimetable || !date || !period || !hallId}
              disabledHint={!hasTimetable ? "No timetable has been generated yet." : undefined}
              onDownload={() =>
                tryDownload(
                  "/exports/attendance-sheets/",
                  `attendance-${date}-${period}.zip`,
                  { date, period, hall_id: hallId },
                )
              }
            />
            )}
            {exports && (
            <ExportCard
              title="Broadsheet"
              description="Full Excel broadsheet across the entire timetable."
              disabled={!hasTimetable}
              disabledHint={!hasTimetable ? "No timetable has been generated yet." : undefined}
              onDownload={() =>
                tryDownload("/exports/broadsheet/", "broadsheet.xlsx")
              }
            />
            )}
          </>
        )}
      </div>
    </div>
  );
}

function ExportCard({
  title,
  description,
  disabled,
  disabledHint,
  onDownload,
}: {
  title: string;
  description: string;
  disabled?: boolean;
  disabledHint?: string;
  onDownload: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardFooter className="flex items-center justify-between gap-3">
        <Button onClick={onDownload} disabled={disabled} variant="outline">
          <Download data-icon="inline-start" /> Download
        </Button>
        {disabled && disabledHint && (
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            {disabledHint}
          </span>
        )}
      </CardFooter>
    </Card>
  );
}
