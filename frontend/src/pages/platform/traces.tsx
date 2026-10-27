import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { AlertOctagon, ChevronLeft, ChevronRight, Clock, Search } from "lucide-react";
import {
  SEGMENT_KIND_LABELS,
  STATUS_CLASS_LABELS,
  TRACE_RANGE_LABELS,
  type Segment,
  type TraceFilters,
  useSchoolOptions,
  useTraces,
} from "@/api/telemetry";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { extractErrorEnvelope } from "@/lib/api";
import { fullTime } from "./errors";

const ANY = "any";
const PLATFORM = "platform";

/** Milliseconds as people read them: 4.2 ms, 380 ms, 2.4 s. */
export function formatMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(ms >= 10_000 ? 0 : 1)} s`;
  if (ms < 10) return `${ms.toFixed(1)} ms`;
  return `${Math.round(ms)} ms`;
}

/** The time of day for a row, with the date when it isn't today. */
export function shortTime(iso: string): string {
  const at = new Date(iso);
  const sameDay = at.toDateString() === new Date().toDateString();
  return sameDay
    ? at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : at.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Error and slow, by label and icon as the design system asks. */
export function SegmentBadges({ segment }: { segment: Pick<Segment, "is_error" | "is_slow" | "truncated"> }) {
  return (
    <span className="inline-flex flex-wrap gap-2">
      {segment.is_error && (
        <Badge variant="destructive">
          <AlertOctagon aria-hidden />
          Error
        </Badge>
      )}
      {segment.is_slow && (
        <Badge variant="warning">
          <Clock aria-hidden />
          Slow
        </Badge>
      )}
      {segment.truncated && <Badge variant="outline">Truncated</Badge>}
    </span>
  );
}

export function SchoolName({ school }: { school: Segment["school"] }) {
  return school ? (
    <span>{school.name}</span>
  ) : (
    <span className="text-muted-foreground">Platform</span>
  );
}

export function FilterSelect<T extends string>({
  label,
  value,
  options,
  onChange,
  anyLabel,
  width = "w-[150px]",
}: {
  label: string;
  value: T | undefined;
  options: Array<[T, string]>;
  onChange: (value: T | undefined) => void;
  anyLabel?: string;
  width?: string;
}) {
  return (
    <Select value={value ?? ANY} onValueChange={(v) => onChange(v === ANY ? undefined : (v as T))}>
      <SelectTrigger className={`h-8 ${width} text-[12px]`} aria-label={`Filter by ${label.toLowerCase()}`}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {anyLabel !== "" && <SelectItem value={ANY}>{anyLabel ?? `Any ${label.toLowerCase()}`}</SelectItem>}
        {options.map(([key, text]) => (
          <SelectItem key={key} value={key}>
            {text}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export const entries = <T extends string>(labels: Record<T, string>) => Object.entries(labels) as Array<[T, string]>;

/**
 * Stored traces, newest first, with filters kept in the address so a link
 * opens the same view (spec 0006, AC-22). Normal traces are sampled; every
 * error and every slow one is kept.
 */
export default function ConsoleTracesPage({ filters }: { filters: TraceFilters }) {
  const navigate = useNavigate();
  // The server's cursors page by position, so new traces arriving don't
  // shift the page being read.
  const [cursor, setCursor] = useState<string | null>(null);
  const traces = useTraces(filters, cursor);
  const schools = useSchoolOptions();
  const rows = traces.data?.results ?? [];
  const filtered = Object.values({ ...filters, range: undefined }).some((v) => v !== undefined && v !== false);

  const setFilters = (patch: Partial<TraceFilters>) => {
    setCursor(null);
    void navigate({
      to: "/console/observability/traces",
      search: { ...filters, ...patch },
      replace: true,
    });
  };

  const schoolOptions: Array<[string, string]> = [
    [PLATFORM, "Platform"],
    ...(schools.data ?? [])
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((s): [string, string] => [String(s.id), s.name]),
  ];

  return (
    <div className="space-y-8">
      <PageHeader
        section="Console · Observability"
        title="Traces"
        description="Every slow or failed request is kept, plus a share of the rest. Open one to see each step it took, across the API and the jobs it started."
      />

      <TraceSearch
        key={filters.trace ?? ""}
        initial={filters.trace ?? ""}
        onSearch={(trace) => setFilters({ trace: trace || undefined })}
      />

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
        <FilterSelect
          label="School"
          value={filters.school}
          options={schoolOptions}
          onChange={(school) => setFilters({ school })}
          anyLabel="All schools"
          width="w-[200px]"
        />
        <FilterSelect
          label="Range"
          value={filters.range ?? "1h"}
          options={entries(TRACE_RANGE_LABELS)}
          onChange={(range) => setFilters({ range: range === "1h" ? undefined : range })}
          anyLabel=""
        />
        <FilterSelect
          label="Kind"
          value={filters.kind}
          options={entries(SEGMENT_KIND_LABELS)}
          onChange={(kind) => setFilters({ kind })}
        />
        <FilterSelect
          label="Status"
          value={filters.status}
          options={entries(STATUS_CLASS_LABELS)}
          onChange={(status) => setFilters({ status })}
        />
        <MinDuration value={filters.min_ms} onChange={(min_ms) => setFilters({ min_ms })} />
        <label className="ml-1 flex items-center gap-2 text-[13px]">
          <Switch
            size="sm"
            checked={!!filters.errors}
            onCheckedChange={(checked) => setFilters({ errors: checked || undefined })}
          />
          Errors only
        </label>
        {filters.route && (
          <Button variant="ghost" size="xs" onClick={() => setFilters({ route: undefined })}>
            Route {filters.route} ×
          </Button>
        )}
      </div>

      <div className="overflow-hidden border border-[color:var(--border)] bg-card">
        {traces.error ? (
          <Alert variant="destructive" className="m-4 w-auto">
            <AlertDescription>{extractErrorEnvelope(traces.error).detail}</AlertDescription>
          </Alert>
        ) : traces.isLoading ? (
          <div className="space-y-2 p-6">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="grid place-items-center px-6 py-14 text-center">
            <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">No traces</p>
            <p className="mt-3 font-serif text-[1.15rem] italic text-muted-foreground">
              {filters.trace
                ? "No kept trace starts with that id. It may not have been sampled, or it has expired."
                : filtered
                  ? "Nothing matches these filters in this range."
                  : "No traces in this range. Try a wider range."}
            </p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>School</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Name</TableHead>
                <TableHead className="text-right">Status</TableHead>
                <TableHead className="text-right">Duration</TableHead>
                <TableHead className="text-right">Spans</TableHead>
                <TableHead>
                  <span className="sr-only">Flags</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TraceRow key={`${row.root_span_id}-${row.started_at}`} row={row} onRoute={(route) => setFilters({ route })} />
              ))}
            </TableBody>
          </Table>
        )}
        {(traces.data?.next || traces.data?.previous) && (
          <div className="flex items-center justify-end gap-2 border-t border-[color:var(--border)] bg-[color:var(--muted)]/40 px-4 py-3">
            <Button
              variant="outline"
              size="sm"
              disabled={!traces.data?.previous || traces.isFetching}
              onClick={() => setCursor(traces.data?.previous ?? null)}
            >
              <ChevronLeft data-icon="inline-start" />
              Newer
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!traces.data?.next || traces.isFetching}
              onClick={() => setCursor(traces.data?.next ?? null)}
            >
              Older
              <ChevronRight data-icon="inline-end" />
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

function TraceRow({ row, onRoute }: { row: Segment; onRoute: (route: string) => void }) {
  return (
    <TableRow>
      <TableCell className="font-mono text-[12px] text-muted-foreground" title={fullTime(row.started_at)}>
        <time dateTime={row.started_at}>{shortTime(row.started_at)}</time>
      </TableCell>
      <TableCell>
        <SchoolName school={row.school} />
      </TableCell>
      <TableCell>{SEGMENT_KIND_LABELS[row.kind]}</TableCell>
      <TableCell className="max-w-[380px]">
        <Link
          to="/console/observability/traces/$traceId"
          params={{ traceId: row.trace_id }}
          className="block truncate font-medium hover:underline"
        >
          {row.root_name}
        </Link>
        {row.route && (
          <button
            type="button"
            onClick={() => onRoute(row.route)}
            className="block truncate font-mono text-[11.5px] text-muted-foreground hover:text-foreground hover:underline"
            title="Show only this route"
          >
            {row.route}
          </button>
        )}
      </TableCell>
      <TableCell
        className={
          row.status_code !== null && row.status_code >= 500
            ? "text-right tabular-nums text-destructive"
            : "text-right tabular-nums"
        }
      >
        {row.status_code ?? "·"}
      </TableCell>
      <TableCell className="text-right tabular-nums">{formatMs(row.duration_ms)}</TableCell>
      <TableCell className="text-right tabular-nums text-muted-foreground">{row.span_count}</TableCell>
      <TableCell>
        <SegmentBadges segment={row} />
      </TableCell>
    </TableRow>
  );
}

/** The reference a school user quotes is the first 8 characters of the id. */
function TraceSearch({ initial, onSearch }: { initial: string; onSearch: (trace: string) => void }) {
  const [value, setValue] = useState(initial);
  const clean = value.trim().toLowerCase();
  const invalid = clean.length > 0 && (clean.length < 8 || !/^[0-9a-f]+$/.test(clean));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!invalid) onSearch(clean);
  };

  return (
    <form onSubmit={submit} className="max-w-xl space-y-1.5" role="search">
      <Label htmlFor="trace-search">Find a trace by reference</Label>
      <div className="relative flex items-center gap-2">
        <Search aria-hidden className="pointer-events-none absolute left-0 size-3.5 text-muted-foreground" />
        <Input
          id="trace-search"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="The reference shown to the user, 8 characters or more"
          className="pl-6 font-mono"
          aria-invalid={invalid || undefined}
          aria-describedby="trace-search-hint"
          spellCheck={false}
          autoComplete="off"
        />
        <Button type="submit" size="sm" variant="outline" disabled={invalid}>
          Find
        </Button>
      </div>
      <p id="trace-search-hint" className={invalid ? "text-[12px] text-destructive" : "text-[12px] text-muted-foreground"}>
        {invalid
          ? "Use at least 8 of the letters and numbers in the reference."
          : "Searches every day still kept, whatever the range."}
      </p>
    </form>
  );
}

function MinDuration({ value, onChange }: { value?: number; onChange: (value: number | undefined) => void }) {
  const [text, setText] = useState(value ? String(value) : "");
  const commit = () => {
    const n = Number.parseInt(text, 10);
    onChange(Number.isFinite(n) && n > 0 ? n : undefined);
  };
  return (
    <label className="flex items-center gap-2 text-[12px] text-muted-foreground">
      At least
      <Input
        value={text}
        onChange={(e) => setText(e.target.value.replace(/\D/g, ""))}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
        inputMode="numeric"
        className="h-8 w-16 text-right text-[12px]"
        aria-label="Minimum duration in milliseconds"
      />
      ms
    </label>
  );
}
