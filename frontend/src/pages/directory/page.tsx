import { useEffect, useState } from "react";
import { Building2, Download, LayoutGrid, List, Printer, Search, Stamp } from "lucide-react";
import {
  DIRECTORY_FORMATS,
  directoryExportQuery,
  useDirectoryPreview,
  type DirectoryDoc,
  type DirectoryFormat,
  type DirectoryScope,
  type Period,
} from "@/api/directory";
import { useTimetableDates } from "@/api/scheduling";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { PageHeader } from "@/components/layout/page-header";
import { downloadAuthenticatedFile } from "@/lib/download";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import { HallSummarySlot, isMatricQuery, type HallView } from "./hall-summary";
import { VisaSlot, type VisaView } from "./visa-view";
import { Eyebrow, Segmented, shortDate, slotId } from "./shared";

const SCOPES: { value: DirectoryScope; label: string }[] = [
  { value: "slot", label: "Slot" },
  { value: "week", label: "Week" },
  { value: "duration", label: "Whole exam" },
];

export default function DirectoryPage() {
  const dates = useTimetableDates();
  const [doc, setDoc] = useState<DirectoryDoc>("hall");
  const [scope, setScope] = useState<DirectoryScope>("slot");
  const [date, setDate] = useState<string | undefined>();
  const [period, setPeriod] = useState<Period>("AM");
  const [format, setFormat] = useState<DirectoryFormat>("pdf");
  const [downloading, setDownloading] = useState(false);
  const [query, setQuery] = useState("");
  const [hallView, setHallView] = useState<HallView>("hall");
  const [visaView, setVisaView] = useState<VisaView>("departments");

  useEffect(() => {
    if (!date && dates.data?.dates.length) setDate(dates.data.dates[0]);
  }, [date, dates.data]);

  const needsDate = scope !== "duration";
  const ready = !needsDate || !!date;
  const preview = useDirectoryPreview({ doc, scope, date, period }, ready);
  const slots = preview.data?.slots ?? [];
  const formatInfo = DIRECTORY_FORMATS.find((f) => f.value === format);

  const onDownload = async () => {
    setDownloading(true);
    try {
      const label = doc === "hall" ? "hall-directory" : "visa";
      await downloadAuthenticatedFile(
        "/directory/export/",
        `${label}-${scope}.${format}`,
        directoryExportQuery({ doc, scope, date, period, format }),
      );
      toast({ title: `${format.toUpperCase()} download started` });
    } catch (err) {
      toast({
        title: "Export failed",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="space-y-8">
      <PageHeader
        section="Operations · Directory & VISA"
        title="Directory & VISA"
        description="See which hall each class sits in and which classes are writing in each slot, then download the documents as PDF, Word or CSV."
      />

      <Tabs value={doc} onValueChange={(v) => setDoc(v as DirectoryDoc)}>
        <TabsList variant="line">
          <TabsTrigger value="hall">
            <Building2 />
            Hall Summary
          </TabsTrigger>
          <TabsTrigger value="visa">
            <Stamp />
            VISA
          </TabsTrigger>
        </TabsList>
      </Tabs>

      {/* Toolbar: what to show on the left, how to download on the right. */}
      <div className="space-y-3 border-b border-[color:var(--border)] pb-4">
        <div className="flex flex-wrap items-end gap-x-5 gap-y-3">
          <div className="space-y-1.5">
            <Eyebrow>Range</Eyebrow>
            <Segmented
              label="Range"
              value={scope}
              options={SCOPES}
              onChange={setScope}
            />
          </div>

          {needsDate && (
            <div className="space-y-1.5">
              <Eyebrow>{scope === "week" ? "Week of" : "Date"}</Eyebrow>
              <Select
                value={date ?? ""}
                onValueChange={(v) => setDate(v || undefined)}
                disabled={!dates.data?.dates.length}
              >
                <SelectTrigger className="h-9 w-[190px]">
                  <SelectValue placeholder="Pick a date" />
                </SelectTrigger>
                <SelectContent>
                  {dates.data?.dates.map((d) => (
                    <SelectItem key={d} value={d}>
                      {shortDate(d)} {d.slice(0, 4)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {scope === "slot" && (
            <div className="space-y-1.5">
              <Eyebrow>Period</Eyebrow>
              <Segmented
                label="Period"
                value={period}
                options={[
                  { value: "AM", label: "AM" },
                  { value: "PM", label: "PM" },
                ]}
                onChange={setPeriod}
              />
            </div>
          )}

          <div className="min-w-[200px] flex-1 space-y-1.5">
            <Eyebrow>Find</Eyebrow>
            <div className="relative">
              <Search className="pointer-events-none absolute left-0 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={
                  doc === "hall"
                    ? "Hall, class or matric number"
                    : "Department or class code"
                }
                className="h-9 pl-6"
              />
            </div>
          </div>

          <div className="flex items-end gap-2">
            <div className="space-y-1.5">
              <Eyebrow>Format</Eyebrow>
              <Select
                value={format}
                onValueChange={(v) => setFormat(v as DirectoryFormat)}
              >
                <SelectTrigger className="h-9 w-[140px]" title={formatInfo?.hint}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {DIRECTORY_FORMATS.map((f) => (
                    <SelectItem key={f.value} value={f.value}>
                      {f.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              onClick={onDownload}
              disabled={!ready || downloading || preview.isLoading}
              title={formatInfo?.hint}
            >
              <Download data-icon="inline-start" />
              {downloading ? "Preparing…" : "Download"}
            </Button>
          </div>
        </div>

        {/* Second row: jump between slots, and switch the layout. */}
        {ready && slots.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3">
            {slots.length > 1 ? (
              <nav
                aria-label="Jump to slot"
                className="flex max-w-full gap-1 overflow-x-auto"
              >
                {slots.map((s) => (
                  <a
                    key={slotId(s)}
                    href={`#${slotId(s)}`}
                    className="shrink-0 border border-border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    {shortDate(s.date)} · {s.period}
                  </a>
                ))}
              </nav>
            ) : (
              <Eyebrow>
                {doc === "hall" && isMatricQuery(query)
                  ? "Showing the hall whose block holds this matric number"
                  : doc === "hall"
                    ? hallView === "hall"
                      ? "Grouped by hall · halls in natural order"
                      : "Grouped by class · halls in matric order"
                    : visaView === "print"
                      ? "Exactly as printed on the PDF"
                      : "Grouped by department"}
              </Eyebrow>
            )}
            {doc === "hall" ? (
              <Segmented
                label="Layout"
                value={hallView}
                onChange={setHallView}
                options={[
                  {
                    value: "hall",
                    label: (
                      <>
                        <LayoutGrid className="size-3" strokeWidth={2.25} />
                        By hall
                      </>
                    ),
                  },
                  {
                    value: "class",
                    label: (
                      <>
                        <List className="size-3" strokeWidth={2.25} />
                        By class
                      </>
                    ),
                  },
                ]}
              />
            ) : (
              <Segmented
                label="Layout"
                value={visaView}
                onChange={setVisaView}
                options={[
                  {
                    value: "departments",
                    label: (
                      <>
                        <LayoutGrid className="size-3" strokeWidth={2.25} />
                        Departments
                      </>
                    ),
                  },
                  {
                    value: "print",
                    label: (
                      <>
                        <Printer className="size-3" strokeWidth={2.25} />
                        Print block
                      </>
                    ),
                  },
                ]}
              />
            )}
          </div>
        )}
      </div>

      {!ready ? (
        <Alert>
          <AlertDescription>Pick a date to preview.</AlertDescription>
        </Alert>
      ) : preview.isLoading ? (
        <div className="space-y-4">
          <Skeleton className="h-16 w-full" />
          <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
            <Skeleton className="h-44" />
            <Skeleton className="h-44" />
            <Skeleton className="h-44" />
          </div>
        </div>
      ) : preview.error ? (
        <Alert variant="destructive">
          <AlertDescription>
            {extractErrorEnvelope(preview.error).detail}
          </AlertDescription>
        </Alert>
      ) : slots.length === 0 ? (
        <Alert>
          <AlertDescription>
            No exam slots found for this range. Generate the timetable
            {doc === "hall" ? " and seat allocation" : ""} first.
          </AlertDescription>
        </Alert>
      ) : (
        <div className="space-y-12">
          {slots.map((slot) =>
            doc === "hall" ? (
              <HallSummarySlot
                key={slotId(slot)}
                slot={slot}
                view={hallView}
                query={query}
              />
            ) : (
              <VisaSlot
                key={slotId(slot)}
                slot={slot}
                view={visaView}
                query={query}
              />
            ),
          )}
        </div>
      )}
    </div>
  );
}
