import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { AlertOctagon, AlertTriangle, ChevronLeft, ChevronRight, Radio, Search } from "lucide-react";
import {
  LOG_LEVEL_LABELS,
  TRACE_RANGE_LABELS,
  levelName,
  type LogFilters,
  type SearchedLogLine,
  useLiveLogs,
  useLogs,
  useSchoolOptions,
  withLiveLines,
} from "@/api/telemetry";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { FilterSelect, SchoolName, entries, shortTime } from "./traces";

const PLATFORM = "platform";
const SEARCH_MIN = 3;

/** Errors and warnings by label and icon, never by colour alone. */
export function LevelBadge({ level }: { level: number }) {
  const name = levelName(level);
  if (level >= 40) {
    return (
      <Badge variant="destructive">
        <AlertOctagon aria-hidden />
        {name}
      </Badge>
    );
  }
  if (level >= 30) {
    return (
      <Badge variant="warning">
        <AlertTriangle aria-hidden />
        {name}
      </Badge>
    );
  }
  return <Badge variant="secondary">{name}</Badge>;
}

/**
 * Ordo's own log lines, newest first, with filters kept in the address so a
 * link opens the same view (spec 0006, AC-23). "Live" adds lines as they are
 * flushed, every 5 seconds.
 */
export default function ConsoleLogsPage({ filters }: { filters: LogFilters }) {
  const navigate = useNavigate();
  const [cursor, setCursor] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const logs = useLogs(filters, cursor);
  const liveLines = useLiveLogs(filters, logs.data?.latest, live && cursor === null);
  const schools = useSchoolOptions();
  const rows = withLiveLines(liveLines, logs.data?.results ?? []);
  const filtered = Object.values({ ...filters, range: undefined }).some((v) => v !== undefined);

  const setFilters = (patch: Partial<LogFilters>) => {
    setCursor(null);
    void navigate({ to: "/console/observability/logs", search: { ...filters, ...patch }, replace: true });
  };

  const page = (next: string | null) => {
    // Live follows the newest lines; paging back stops it.
    setLive(false);
    setCursor(next);
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
        title="Logs"
        description="Ordo's own log lines, with the school, request and trace each came from. Every line is kept, whether or not its trace was."
      />

      <LogSearch
        key={`${filters.q ?? ""}|${filters.logger ?? ""}`}
        initialText={filters.q ?? ""}
        initialLogger={filters.logger ?? ""}
        onSearch={(q, logger) => setFilters({ q: q || undefined, logger: logger || undefined })}
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
          label="Level"
          value={filters.level}
          options={entries(LOG_LEVEL_LABELS)}
          onChange={(level) => setFilters({ level })}
          anyLabel="Any level"
          width="w-[170px]"
        />
        <label className="ml-1 flex items-center gap-2 text-[13px]">
          <Switch
            size="sm"
            checked={live}
            onCheckedChange={(checked) => {
              setCursor(null);
              setLive(checked);
            }}
          />
          Live
        </label>
        {filters.trace && (
          <Button variant="ghost" size="xs" onClick={() => setFilters({ trace: undefined })}>
            Trace {filters.trace.slice(0, 8)} ×
          </Button>
        )}
        {filters.request && (
          <Button variant="ghost" size="xs" onClick={() => setFilters({ request: undefined })}>
            Request {filters.request.slice(0, 8)} ×
          </Button>
        )}
        {filters.user !== undefined && (
          <Button variant="ghost" size="xs" onClick={() => setFilters({ user: undefined })}>
            User {filters.user} ×
          </Button>
        )}
      </div>

      <p aria-live="polite" className="sr-only">
        {liveLines.length ? `${liveLines.length} new lines` : ""}
      </p>

      <div className="overflow-hidden border border-[color:var(--border)] bg-card">
        {live && cursor === null && (
          <p className="flex items-center gap-2 border-b border-[color:var(--border)] bg-[color:var(--muted)]/40 px-4 py-2 text-[12px] text-muted-foreground">
            <Radio aria-hidden className="size-3.5" />
            Live: new lines appear at the top as they arrive.
          </p>
        )}
        {logs.error ? (
          <Alert variant="destructive" className="m-4 w-auto">
            <AlertDescription>{extractErrorEnvelope(logs.error).detail}</AlertDescription>
          </Alert>
        ) : logs.isLoading ? (
          <div className="space-y-2 p-6">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="grid place-items-center px-6 py-14 text-center">
            <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">No logs</p>
            <p className="mt-3 font-serif text-[1.15rem] italic text-muted-foreground">
              {filtered ? "Nothing matches these filters in this range." : "No log lines in this range. Try a wider range."}
            </p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Level</TableHead>
                <TableHead>School</TableHead>
                <TableHead>Message</TableHead>
                <TableHead>Trace</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((line) => (
                <LogRow
                  key={line.id}
                  line={line}
                  onTrace={(trace) => setFilters({ trace })}
                  onLogger={(logger) => setFilters({ logger })}
                />
              ))}
            </TableBody>
          </Table>
        )}
        {(logs.data?.next || logs.data?.previous) && (
          <div className="flex items-center justify-end gap-2 border-t border-[color:var(--border)] bg-[color:var(--muted)]/40 px-4 py-3">
            <Button
              variant="outline"
              size="sm"
              disabled={!logs.data?.previous || logs.isFetching}
              onClick={() => page(logs.data?.previous ?? null)}
            >
              <ChevronLeft data-icon="inline-start" />
              Newer
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!logs.data?.next || logs.isFetching}
              onClick={() => page(logs.data?.next ?? null)}
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

function LogRow({
  line,
  onTrace,
  onLogger,
}: {
  line: SearchedLogLine;
  onTrace: (trace: string) => void;
  onLogger: (logger: string) => void;
}) {
  return (
    <TableRow className="align-top">
      <TableCell className="whitespace-nowrap font-mono text-[12px] text-muted-foreground" title={fullTime(line.logged_at)}>
        <time dateTime={line.logged_at}>{shortTime(line.logged_at)}</time>
      </TableCell>
      <TableCell>
        <LevelBadge level={line.level} />
      </TableCell>
      <TableCell className="whitespace-nowrap">
        <SchoolName school={line.school} />
      </TableCell>
      <TableCell className="min-w-[320px] max-w-[640px]">
        <p className="whitespace-pre-wrap break-words font-mono text-[12.5px]">{line.message}</p>
        <button
          type="button"
          onClick={() => onLogger(line.logger)}
          className="mt-0.5 font-mono text-[11px] text-muted-foreground hover:text-foreground hover:underline"
          title="Show only this logger"
        >
          {line.logger}
        </button>
        {(line.user_id !== null || line.request_id) && (
          <span className="ml-3 font-mono text-[11px] text-muted-foreground">
            {line.user_id !== null && `user ${line.user_id}`}
            {line.user_id !== null && line.request_id && " · "}
            {line.request_id && `request ${line.request_id.slice(0, 8)}`}
          </span>
        )}
      </TableCell>
      <TableCell className="whitespace-nowrap text-[12px]">
        {!line.trace_id ? (
          <span className="text-muted-foreground">None</span>
        ) : (
          <span className="flex flex-col items-start gap-0.5">
            {line.trace_stored ? (
              <Link
                to="/console/observability/traces/$traceId"
                params={{ traceId: line.trace_id }}
                className="font-mono hover:underline"
              >
                {line.trace_id.slice(0, 8)}
              </Link>
            ) : (
              <span className="font-mono text-muted-foreground" title="Logs are always kept; this trace was not sampled.">
                {line.trace_id.slice(0, 8)}, not sampled
              </span>
            )}
            <button
              type="button"
              onClick={() => onTrace(line.trace_id!)}
              className="text-[11px] text-muted-foreground hover:text-foreground hover:underline"
            >
              Lines from this trace
            </button>
          </span>
        )}
      </TableCell>
    </TableRow>
  );
}

function LogSearch({
  initialText,
  initialLogger,
  onSearch,
}: {
  initialText: string;
  initialLogger: string;
  onSearch: (text: string, logger: string) => void;
}) {
  const [text, setText] = useState(initialText);
  const [logger, setLogger] = useState(initialLogger);
  const clean = text.trim();
  const tooShort = clean.length > 0 && clean.length < SEARCH_MIN;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!tooShort) onSearch(clean, logger.trim());
  };

  return (
    <form onSubmit={submit} className="flex max-w-3xl flex-wrap items-start gap-3" role="search">
      <div className="min-w-[260px] flex-1 space-y-1.5">
        <Label htmlFor="log-search">Search messages</Label>
        <div className="relative flex items-center">
          <Search aria-hidden className="pointer-events-none absolute left-0 size-3.5 text-muted-foreground" />
          <Input
            id="log-search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Part of a word or an id"
            className="pl-6"
            aria-invalid={tooShort || undefined}
            aria-describedby="log-search-hint"
            spellCheck={false}
            autoComplete="off"
          />
        </div>
        <p id="log-search-hint" className={tooShort ? "text-[12px] text-destructive" : "text-[12px] text-muted-foreground"}>
          {tooShort ? `Use at least ${SEARCH_MIN} characters.` : "Finds the text anywhere in a message, in any case."}
        </p>
      </div>
      <div className="w-[220px] space-y-1.5">
        <Label htmlFor="log-logger">Logger</Label>
        <Input
          id="log-logger"
          value={logger}
          onChange={(e) => setLogger(e.target.value)}
          placeholder="ems.tasks"
          className="font-mono"
          spellCheck={false}
          autoComplete="off"
        />
      </div>
      <Button type="submit" size="sm" variant="outline" className="mt-[22px]" disabled={tooShort}>
        Search
      </Button>
    </form>
  );
}
