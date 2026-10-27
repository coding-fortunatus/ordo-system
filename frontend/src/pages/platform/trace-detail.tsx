import { Fragment, useMemo, useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { AlertOctagon, ArrowLeft, Check, CircleDashed, Copy, ScrollText } from "lucide-react";
import {
  SEGMENT_KIND_LABELS,
  SPAN_KIND_LABELS,
  type Segment,
  type Span,
  type SpanKind,
  type TraceDetail,
  useTrace,
} from "@/api/telemetry";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { extractErrorEnvelope } from "@/lib/api";
import { fullTime } from "./errors";
import { LevelBadge } from "./logs";
import { SchoolName, SegmentBadges, formatMs, shortTime } from "./traces";

// The detail endpoint lists at most this many of a trace's lines.
const TRACE_LOGS_MAX = 500;

/** Kinds differ by lightness on the neutral chart ramp, and by the legend. */
const KIND_FILL: Record<SpanKind, string> = {
  server: "var(--chart-4)",
  job: "var(--chart-5)",
  ws: "var(--chart-4)",
  db: "var(--chart-2)",
  redis: "var(--chart-3)",
  http_out: "var(--chart-3)",
  internal: "var(--chart-1)",
};

const INDENT_PX = 14;
const MAX_DEPTH = 12;

type Row =
  | { type: "segment"; segment: Segment }
  | { type: "missing"; key: string; depth: number }
  | { type: "span"; span: Span; depth: number; start: number; end: number };

function ms(iso: string): number {
  return new Date(iso).getTime();
}

/**
 * One row per span: each segment opens a labelled group, spans nest under
 * their parent, and a parent that wasn't stored gets a "Not sampled" row.
 */
function waterfallRows(detail: TraceDetail): { rows: Row[]; origin: number; total: number } {
  const spansBySegment = new Map<string, Span[]>();
  for (const span of detail.spans) {
    const list = spansBySegment.get(span.root_span_id) ?? [];
    list.push(span);
    spansBySegment.set(span.root_span_id, list);
  }
  const missing = new Set(detail.missing_parents);
  const origin = Math.min(...detail.segments.map((s) => ms(s.started_at)), ...detail.spans.map((s) => ms(s.started_at)));
  let latest = origin;
  const rows: Row[] = [];

  for (const segment of detail.segments) {
    rows.push({ type: "segment", segment });
    const spans = spansBySegment.get(segment.root_span_id) ?? [];
    const ids = new Set(spans.map((s) => s.span_id));
    const children = new Map<string, Span[]>();
    const tops: Span[] = [];
    for (const span of spans) {
      if (span.parent_span_id && ids.has(span.parent_span_id)) {
        const list = children.get(span.parent_span_id) ?? [];
        list.push(span);
        children.set(span.parent_span_id, list);
      } else {
        tops.push(span);
      }
    }
    const walk = (span: Span, depth: number) => {
      const start = ms(span.started_at) - origin;
      const end = start + span.duration_ms;
      latest = Math.max(latest, origin + end);
      rows.push({ type: "span", span, depth, start, end });
      for (const child of children.get(span.span_id) ?? []) walk(child, depth + 1);
    };
    for (const top of tops) {
      // A parent in another stored segment is shown there; only a parent
      // nobody stored gets the placeholder.
      if (top.parent_span_id && missing.has(top.parent_span_id)) {
        rows.push({ type: "missing", key: `${segment.root_span_id}-${top.parent_span_id}`, depth: 0 });
        walk(top, 1);
      } else {
        walk(top, 0);
      }
    }
  }
  return { rows, origin, total: Math.max(latest - origin, 1) };
}

/**
 * One trace: every stored segment, the span waterfall, and its logs
 * (spec 0006, AC-22).
 */
export default function ConsoleTraceDetailPage({ traceId }: { traceId: string }) {
  const trace = useTrace(traceId);

  return (
    <div className="space-y-8">
      <div>
        <Button variant="ghost" size="sm" render={<Link to="/console/observability/traces" />}>
          <ArrowLeft data-icon="inline-start" />
          Traces
        </Button>
      </div>
      <PageHeader
        section="Console · Observability · Trace"
        title={trace.data?.segments[0]?.root_name ?? "Trace"}
        meta={<TraceId id={traceId} />}
      />
      {trace.isLoading ? (
        <div className="space-y-4">
          <Skeleton className="h-24" />
          <Skeleton className="h-72" />
        </div>
      ) : trace.error || !trace.data ? (
        <Alert variant="destructive">
          <AlertOctagon aria-hidden />
          <AlertTitle>This trace can't be shown</AlertTitle>
          <AlertDescription>{extractErrorEnvelope(trace.error).detail}</AlertDescription>
        </Alert>
      ) : (
        <TraceBody detail={trace.data} />
      )}
    </div>
  );
}

function TraceBody({ detail }: { detail: TraceDetail }) {
  const { rows, origin, total } = useMemo(() => waterfallRows(detail), [detail]);
  const [selected, setSelected] = useState<string | null>(
    () => detail.spans.find((s) => s.status === "error")?.span_id ?? null,
  );
  const selectedSpan = detail.spans.find((s) => s.span_id === selected) ?? null;
  const kinds = [...new Set(detail.spans.map((s) => s.kind))];

  return (
    <>
      <section aria-labelledby="segments-heading" className="space-y-3">
        <h2 id="segments-heading" className="text-xs font-semibold uppercase tracking-wider">
          {detail.segments.length === 1 ? "1 stored part" : `${detail.segments.length} stored parts`}
        </h2>
        <ul className="grid gap-3 lg:grid-cols-2">
          {detail.segments.map((segment) => (
            <li key={segment.root_span_id}>
              <SegmentCard segment={segment} />
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="waterfall-heading" className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 id="waterfall-heading" className="text-xs font-semibold uppercase tracking-wider">
            Steps, {formatMs(total)} in all
          </h2>
          <Legend kinds={kinds} />
        </div>
        <div className="overflow-x-auto border border-[color:var(--border)] bg-card">
          <div className="min-w-[640px]" role="list" aria-label="Spans in start order">
            <TimeScale total={total} />
            {rows.map((row) =>
              row.type === "segment" ? (
                <SegmentRow key={`seg-${row.segment.root_span_id}`} segment={row.segment} origin={origin} />
              ) : row.type === "missing" ? (
                <MissingRow key={`missing-${row.key}`} depth={row.depth} />
              ) : (
                <SpanRow
                  key={row.span.span_id}
                  row={row}
                  total={total}
                  selected={row.span.span_id === selected}
                  onSelect={() => setSelected(row.span.span_id === selected ? null : row.span.span_id)}
                />
              ),
            )}
          </div>
        </div>
        {selectedSpan ? (
          <SpanDetail span={selectedSpan} />
        ) : (
          <p className="text-[12px] text-muted-foreground">Choose a step to see what it recorded.</p>
        )}
      </section>

      <section aria-labelledby="logs-heading" className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 id="logs-heading" className="text-xs font-semibold uppercase tracking-wider">
            {detail.logs.length >= TRACE_LOGS_MAX ? `Logs, the first ${TRACE_LOGS_MAX}` : "Logs"}
          </h2>
          {detail.logs.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              render={<Link to="/console/observability/logs" search={{ trace: detail.trace_id }} />}
            >
              <ScrollText data-icon="inline-start" />
              Search these logs
            </Button>
          )}
        </div>
        {detail.logs.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">No logs are stored for this trace.</p>
        ) : (
          <ol className="divide-y divide-[color:var(--border)] border border-[color:var(--border)] bg-card">
            {detail.logs.map((log) => (
              <li
                key={log.id}
                className="grid grid-cols-[80px_86px_1fr] items-start gap-3 px-4 py-2 sm:grid-cols-[80px_86px_minmax(140px,220px)_1fr]"
              >
                <time
                  dateTime={log.logged_at}
                  title={fullTime(log.logged_at)}
                  className="pt-0.5 font-mono text-[12px] text-muted-foreground"
                >
                  {shortTime(log.logged_at)}
                </time>
                <span>
                  <LevelBadge level={log.level} />
                </span>
                <span className="hidden truncate pt-0.5 font-mono text-[11.5px] text-muted-foreground sm:block" title={log.logger}>
                  {log.logger}
                </span>
                <span className="whitespace-pre-wrap break-words font-mono text-[12px]">{log.message}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </>
  );
}

function TraceId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard refused (an insecure origin); the id stays selectable.
    }
  };
  return (
    <span className="inline-flex items-center gap-2">
      <code className="select-all break-all font-mono text-[12px] text-muted-foreground">{id}</code>
      <Button variant="ghost" size="icon-xs" onClick={() => void copy()} aria-label="Copy trace id">
        {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
      </Button>
      <span aria-live="polite" className="sr-only">
        {copied ? "Copied" : ""}
      </span>
    </span>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate">{children}</dd>
    </div>
  );
}

function SegmentCard({ segment }: { segment: Segment }) {
  return (
    <article className="h-full border border-[color:var(--border)] bg-card px-4 py-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">
          {SEGMENT_KIND_LABELS[segment.kind]} · <SchoolName school={segment.school} />
        </span>
        <SegmentBadges segment={segment} />
      </header>
      <p className="mt-1 truncate font-mono text-[12px] text-muted-foreground">{segment.root_name}</p>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-[13px] sm:grid-cols-3">
        <Fact label="Started">
          <time dateTime={segment.started_at} title={fullTime(segment.started_at)}>
            {shortTime(segment.started_at)}
          </time>
        </Fact>
        <Fact label="Duration">{formatMs(segment.duration_ms)}</Fact>
        <Fact label="Status">{segment.status_code ?? "None"}</Fact>
        <Fact label="Release">
          <span className="font-mono text-[12px]">{segment.release || "Unknown"}</span>
        </Fact>
        <Fact label="User id">{segment.user_id ?? "None"}</Fact>
        <Fact label="Request id">
          <span className="font-mono text-[12px]" title={segment.request_id}>
            {segment.request_id ? segment.request_id.slice(0, 12) : "None"}
          </span>
        </Fact>
      </dl>
    </article>
  );
}

function Legend({ kinds }: { kinds: SpanKind[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted-foreground" aria-label="Kinds of step">
      {kinds.map((kind) => (
        <li key={kind} className="inline-flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-2.5 w-4" style={{ background: KIND_FILL[kind] }} />
          {SPAN_KIND_LABELS[kind]}
        </li>
      ))}
      <li className="inline-flex items-center gap-1.5">
        <AlertOctagon aria-hidden className="size-3 text-destructive" />
        Failed
      </li>
    </ul>
  );
}

const NAME_COLUMN = "w-[38%] min-w-[220px]";

function TimeScale({ total }: { total: number }) {
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <div className="flex border-b border-[color:var(--border)] text-[10.5px] text-muted-foreground" aria-hidden>
      <div className={`${NAME_COLUMN} shrink-0 px-3 py-2 font-mono uppercase tracking-[0.14em]`}>Step</div>
      <div className="relative flex-1 py-2 pr-3">
        {ticks.map((t) => (
          <span
            key={t}
            className="absolute top-2 font-mono tabular-nums"
            style={{ left: `${t * 100}%`, transform: t === 0 ? undefined : t === 1 ? "translateX(-100%)" : "translateX(-50%)" }}
          >
            {formatMs(total * t)}
          </span>
        ))}
        &nbsp;
      </div>
    </div>
  );
}

function SegmentRow({ segment, origin }: { segment: Segment; origin: number }) {
  const offset = ms(segment.started_at) - origin;
  return (
    <div
      role="listitem"
      className="flex items-center gap-0 border-b border-[color:var(--border)] bg-[color:var(--muted)]/40 text-[12px]"
    >
      <div className={`${NAME_COLUMN} shrink-0 truncate px-3 py-1.5 font-semibold`}>
        {SEGMENT_KIND_LABELS[segment.kind]} · <SchoolName school={segment.school} />
        <span className="ml-2 font-mono font-normal text-muted-foreground">{segment.process_role}</span>
      </div>
      <div className="flex-1 px-0 py-1.5 pr-3 font-mono text-[11px] text-muted-foreground">
        +{formatMs(offset)}
      </div>
    </div>
  );
}

function MissingRow({ depth }: { depth: number }) {
  return (
    <div role="listitem" className="flex items-center border-b border-[color:var(--border)] text-[12.5px] text-muted-foreground">
      <div
        className={`${NAME_COLUMN} shrink-0 truncate py-1.5 pr-3 italic`}
        style={{ paddingLeft: 12 + depth * INDENT_PX }}
      >
        <CircleDashed aria-hidden className="mr-1.5 inline size-3.5 align-[-2px]" />
        Not sampled
      </div>
      <div className="flex-1 py-1.5 pr-3 text-[11.5px]">
        The step that started this one wasn't stored (it was in the browser, or not kept).
      </div>
    </div>
  );
}

function SpanRow({
  row,
  total,
  selected,
  onSelect,
}: {
  row: Extract<Row, { type: "span" }>;
  total: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const { span, depth, start } = row;
  const failed = span.status === "error";
  const left = (start / total) * 100;
  const width = Math.max((span.duration_ms / total) * 100, 0.4);
  const label = `${span.name}, ${SPAN_KIND_LABELS[span.kind]}, ${formatMs(span.duration_ms)}, starting at ${formatMs(start)}${failed ? ", failed" : ""}`;

  return (
    <div role="listitem" className="border-b border-[color:var(--border)] last:border-b-0">
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        aria-label={label}
        className={`flex w-full items-center text-left text-[12.5px] outline-none hover:bg-[color:var(--muted)]/50 focus-visible:bg-[color:var(--muted)] focus-visible:ring-2 focus-visible:ring-ring/30 ${selected ? "bg-[color:var(--muted)]" : ""}`}
      >
        <span
          className={`${NAME_COLUMN} flex shrink-0 items-center gap-1.5 truncate py-1.5 pr-3`}
          style={{ paddingLeft: 12 + Math.min(depth, MAX_DEPTH) * INDENT_PX }}
        >
          {failed && <AlertOctagon aria-hidden className="size-3.5 shrink-0 text-destructive" />}
          <span className={`truncate ${failed ? "text-destructive" : ""}`}>{span.name}</span>
        </span>
        <span className="relative h-7 flex-1 pr-3">
          <span
            aria-hidden
            className="absolute top-1/2 h-3 -translate-y-1/2"
            style={{
              left: `${left}%`,
              width: `${width}%`,
              background: failed ? "var(--destructive)" : KIND_FILL[span.kind],
            }}
          />
          <span
            aria-hidden
            className="absolute top-1/2 -translate-y-1/2 whitespace-nowrap pl-1.5 font-mono text-[11px] tabular-nums text-muted-foreground"
            style={left + width > 82 ? { right: `${100 - left}%`, paddingRight: 6 } : { left: `${left + width}%` }}
          >
            {formatMs(span.duration_ms)}
          </span>
        </span>
      </button>
    </div>
  );
}

function SpanDetail({ span }: { span: Span }) {
  const attributes = Object.entries(span.attributes);
  return (
    <article className="border border-[color:var(--border)] bg-card" aria-label={`Step ${span.name}`}>
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-[color:var(--border)] px-4 py-3">
        <span className="break-all font-medium">{span.name}</span>
        <span className="font-mono text-[11.5px] text-muted-foreground">
          {SPAN_KIND_LABELS[span.kind]} · {formatMs(span.duration_ms)} · {span.span_id}
        </span>
      </header>
      {attributes.length === 0 ? (
        <p className="px-4 py-3 text-[12.5px] text-muted-foreground">Nothing else was recorded for this step.</p>
      ) : (
        <dl className="grid grid-cols-[minmax(120px,180px)_1fr] gap-x-4 gap-y-1.5 px-4 py-3 text-[12.5px]">
          {attributes.map(([key, value]) => (
            <Fragment key={key}>
              <dt className="pt-0.5 font-mono text-[11px] text-muted-foreground">{key}</dt>
              <dd className="min-w-0 whitespace-pre-wrap break-all font-mono">
                {Array.isArray(value) ? value.join(", ") : String(value)}
              </dd>
            </Fragment>
          ))}
        </dl>
      )}
    </article>
  );
}
