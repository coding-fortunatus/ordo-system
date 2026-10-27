import { type KeyboardEvent, useEffect, useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { LayoutGrid } from "lucide-react";
import {
  AGGREGATION_LABELS,
  type Aggregation,
  type ChartInput,
  METRIC_RANGE_LABELS,
  type MetricQuery,
  type MetricQueryResult,
  type MetricRange,
  type RegistryMetric,
  useAddChart,
  useCreateDashboard,
  useDashboards,
  useMetricQuery,
  useMetricRegistry,
  useSchoolOptions,
} from "@/api/telemetry";
import { PageHeader } from "@/components/layout/page-header";
import { type ChartSeries, TimeSeriesChart } from "@/components/time-series-chart";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import { formatFor } from "./observability";
import { FilterSelect, entries } from "./traces";

export interface MetricsSearch {
  metric?: string;
  agg?: Aggregation;
  group?: string;
  filters?: Record<string, string>;
  school?: number;
  range?: MetricRange;
}

const DEFAULT_METRIC = "http.server.requests";

/** A line's name: the school's name, its label value, or ``fallback`` when ungrouped. */
type Lines = Pick<MetricQueryResult, "series" | "schools">;

export function lineLabel(labels: Record<string, string | number | null>, result: Lines | undefined, fallback: string) {
  const [key, value] = Object.entries(labels)[0] ?? [];
  if (key === undefined) return fallback;
  if (value === null || value === "") return key === "school" ? "Platform" : "none";
  if (key === "school" && typeof value === "number") return result?.schools?.[String(value)]?.name ?? `School ${value}`;
  return String(value);
}

export function toChartSeries(result: Lines | undefined, fallback: string): ChartSeries[] {
  return (result?.series ?? []).map((line) => ({ label: lineLabel(line.labels, result, fallback), points: line.points }));
}

/** The query a set of choices makes, kept within what the metric allows. */
export function resolveQuery(metric: RegistryMetric, choices: Omit<MetricQuery, "metric" | "aggregation"> & { aggregation?: string }): MetricQuery {
  const aggregation = (metric.aggregations as string[]).includes(choices.aggregation ?? "")
    ? (choices.aggregation as Aggregation)
    : metric.aggregations[0];
  const filters = Object.fromEntries(
    Object.entries(choices.filters ?? {}).filter(([k, v]) => {
      if (!(k in metric.labels) || !v) return false;
      const allowed = metric.labels[k];
      return allowed === null || allowed.includes(v);
    }),
  );
  return {
    metric: metric.name,
    aggregation,
    group_by: choices.group_by && metric.group_by.includes(choices.group_by) ? choices.group_by : undefined,
    filters,
    school: metric.scope === "school" ? choices.school : undefined,
  };
}

/** A free text label filter, applied on Enter or when the field loses focus. */
function TextFilter({ label, value, onChange }: { label: string; value: string | undefined; onChange: (v: string | undefined) => void }) {
  const [text, setText] = useState(value ?? "");
  useEffect(() => setText(value ?? ""), [value]);
  const commit = () => onChange(text.trim() || undefined);
  return (
    <Input
      className="h-8 w-[180px] font-mono text-[12px]"
      placeholder={`Any ${label}`}
      aria-label={`Filter by ${label}`}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e: KeyboardEvent) => e.key === "Enter" && commit()}
    />
  );
}

/** One select or text box per label of ``metric``. */
export function LabelFilters({
  metric,
  value,
  onChange,
}: {
  metric: RegistryMetric;
  value: Record<string, string>;
  onChange: (filters: Record<string, string>) => void;
}) {
  const set = (name: string, v: string | undefined) => {
    const next = { ...value };
    if (v) next[name] = v;
    else delete next[name];
    onChange(next);
  };
  return (
    <>
      {Object.entries(metric.labels).map(([name, allowed]) =>
        allowed ? (
          <FilterSelect
            key={name}
            label={name.replace(/_/g, " ")}
            value={value[name]}
            options={allowed.map((v) => [v, v] as [string, string])}
            onChange={(v) => set(name, v)}
          />
        ) : (
          <TextFilter key={name} label={name.replace(/_/g, " ")} value={value[name]} onChange={(v) => set(name, v)} />
        ),
      )}
    </>
  );
}

function summary(points: Array<[number, number | null]>) {
  const values = points.map(([, v]) => v).filter((v): v is number => v != null);
  if (!values.length) return null;
  return {
    latest: values[values.length - 1],
    average: values.reduce((a, b) => a + b, 0) / values.length,
    highest: Math.max(...values),
  };
}

export function chartTitle(query: Pick<MetricQuery, "metric" | "aggregation" | "group_by">) {
  const by = query.group_by ? ` by ${query.group_by.replace(/_/g, " ")}` : "";
  return `${AGGREGATION_LABELS[query.aggregation]} of ${query.metric}${by}`;
}

/**
 * Any declared metric, aggregated and grouped as the admin picks, over a
 * time range (spec 0006, AC-24). Choices live in the address, so a link
 * opens the same chart, and any chart can be saved to a shared dashboard.
 */
export default function ConsoleMetricsPage({ search }: { search: MetricsSearch }) {
  const navigate = useNavigate();
  const registry = useMetricRegistry();
  const schools = useSchoolOptions();
  const range = search.range ?? "24h";
  const [saving, setSaving] = useState(false);

  const metric = useMemo(() => {
    const all = registry.data ?? [];
    return all.find((m) => m.name === search.metric) ?? all.find((m) => m.name === DEFAULT_METRIC) ?? all[0];
  }, [registry.data, search.metric]);
  const query = metric
    ? resolveQuery(metric, {
        aggregation: search.agg,
        group_by: search.group,
        filters: search.filters,
        school: search.school,
      })
    : null;
  const result = useMetricQuery(query, range);
  const format = formatFor(metric?.unit ?? "1", query?.aggregation ?? "sum");
  const title = query ? chartTitle(query) : "";
  const series = toChartSeries(result.data, metric?.name ?? "");

  const setSearch = (patch: MetricsSearch) =>
    void navigate({ to: "/console/observability/metrics", search: { ...search, ...patch }, replace: true });

  const schoolOptions: Array<[string, string]> = (schools.data ?? [])
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((s) => [String(s.id), s.name]);

  return (
    <div className="space-y-8">
      <PageHeader
        section="Console · Observability"
        title="Metrics"
        description="Chart any metric Ordo records, for every school together or one, and save the chart to a shared dashboard."
        actions={
          query && (
            <Button variant="brand" onClick={() => setSaving(true)}>
              <LayoutGrid data-icon="inline-start" />
              Save to dashboard
            </Button>
          )
        }
      />

      {registry.error ? (
        <Alert variant="destructive">
          <AlertDescription>{extractErrorEnvelope(registry.error).detail}</AlertDescription>
        </Alert>
      ) : null}

      {!metric ? (
        <Skeleton className="h-8 w-full max-w-xl" />
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Chart">
            <FilterSelect
              label="Metric"
              value={metric.name}
              options={(registry.data ?? []).map((m) => [m.name, m.name] as [string, string])}
              onChange={(v) => setSearch({ metric: v, agg: undefined, group: undefined, filters: undefined })}
              anyLabel=""
              width="w-[260px]"
            />
            <FilterSelect
              label="Aggregation"
              value={query?.aggregation}
              options={metric.aggregations.map((a) => [a, AGGREGATION_LABELS[a]] as [Aggregation, string])}
              onChange={(v) => setSearch({ agg: v })}
              anyLabel=""
            />
            <FilterSelect
              label="Group"
              value={query?.group_by}
              options={metric.group_by.map((g) => [g, `By ${g.replace(/_/g, " ")}`] as [string, string])}
              onChange={(v) => setSearch({ group: v })}
              anyLabel="No grouping"
              width="w-[170px]"
            />
            <FilterSelect
              label="Range"
              value={range}
              options={entries(METRIC_RANGE_LABELS)}
              onChange={(v) => setSearch({ range: v === "24h" ? undefined : v })}
              anyLabel=""
            />
            {metric.scope === "school" ? (
              <FilterSelect
                label="School"
                value={search.school === undefined ? undefined : String(search.school)}
                options={schoolOptions}
                onChange={(v) => setSearch({ school: v ? Number(v) : undefined })}
                anyLabel="All schools"
                width="w-[200px]"
              />
            ) : (
              <span className="text-[12px] text-muted-foreground">The whole platform: this metric has no per school values.</span>
            )}
          </div>
          {Object.keys(metric.labels).length > 0 && (
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
              <LabelFilters
                metric={metric}
                value={query?.filters ?? {}}
                onChange={(filters) => setSearch({ filters: Object.keys(filters).length ? filters : undefined })}
              />
            </div>
          )}
          <p className="text-[13px] text-muted-foreground">
            {metric.description || metric.name} · a {metric.type}
            {metric.unit !== "1" ? `, in ${metric.unit}` : ""}.
          </p>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="font-mono text-[13px]">{title}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          {result.error ? (
            <Alert variant="destructive">
              <AlertDescription>{extractErrorEnvelope(result.error).detail}</AlertDescription>
            </Alert>
          ) : result.isLoading || !result.data ? (
            <Skeleton className="h-[280px] w-full" />
          ) : (
            <>
              <TimeSeriesChart
                series={series}
                label={title}
                format={format}
                height={280}
                dropped={result.data.dropped}
                step={result.data.step}
              />
              {series.length > 0 && (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Series</TableHead>
                      <TableHead className="text-right">Latest</TableHead>
                      <TableHead className="text-right">Average</TableHead>
                      <TableHead className="text-right">Highest</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {series.map((s) => {
                      const sum = summary(s.points);
                      return (
                        <TableRow key={s.label}>
                          <TableCell className="font-mono text-[12px]">{s.label}</TableCell>
                          {sum ? (
                            <>
                              <TableCell className="text-right tabular-nums">{format(sum.latest)}</TableCell>
                              <TableCell className="text-right tabular-nums">{format(sum.average)}</TableCell>
                              <TableCell className="text-right tabular-nums">{format(sum.highest)}</TableCell>
                            </>
                          ) : (
                            <TableCell colSpan={3} className="text-right text-muted-foreground">
                              No data
                            </TableCell>
                          )}
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {query && (
        <SaveChartDialog
          open={saving}
          onClose={() => setSaving(false)}
          chart={{
            title,
            metric_name: query.metric,
            aggregation: query.aggregation,
            group_by: query.group_by ?? "",
            filters: query.filters ?? {},
            // Kept only when picked here; otherwise the chart follows the
            // dashboard's school picker.
            school: query.school ?? null,
          }}
        />
      )}
    </div>
  );
}

const NEW = "__new__";

function SaveChartDialog({ open, onClose, chart }: { open: boolean; onClose: () => void; chart: ChartInput }) {
  const navigate = useNavigate();
  const dashboards = useDashboards();
  const create = useCreateDashboard();
  const add = useAddChart();
  const [title, setTitle] = useState(chart.title);
  const [target, setTarget] = useState<string>(NEW);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const busy = create.isPending || add.isPending;

  useEffect(() => {
    if (!open) return;
    setTitle(chart.title);
    setName("");
    setError(null);
    setTarget(dashboards.data?.length ? String(dashboards.data[0].id) : NEW);
    // Reset when the dialog opens, not on every refetch.
  }, [open]);

  const options: Array<[string, string]> = [
    ...(dashboards.data ?? []).map((d) => [String(d.id), d.name] as [string, string]),
    [NEW, "New dashboard…"],
  ];

  const save = async () => {
    setError(null);
    const saved = { ...chart, title: title.trim() || chart.title };
    try {
      const dashboard =
        target === NEW
          ? await create.mutateAsync({ name: name.trim(), charts: [saved] })
          : await add.mutateAsync({ id: Number(target), chart: saved });
      toast({ title: `Saved to ${dashboard.name}` });
      onClose();
      void navigate({ to: "/console/observability/dashboards/$id", params: { id: String(dashboard.id) } });
    } catch (err) {
      const envelope = extractErrorEnvelope(err);
      setError(Object.values(envelope.errors ?? {}).flat().join(" ") || envelope.detail);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save to dashboard</DialogTitle>
          <DialogDescription>
            Every admin sees shared dashboards. The chart uses the dashboard&apos;s time range
            {chart.school === null ? " and follows its school picker." : ", and stays on the school picked here."}
          </DialogDescription>
        </DialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <div className="space-y-2">
            <Label htmlFor="chart-title">Chart title</Label>
            <Input id="chart-title" value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label>Dashboard</Label>
            <FilterSelect label="Dashboard" value={target} options={options} onChange={(v) => setTarget(v ?? NEW)} anyLabel="" width="w-full" />
          </div>
          {target === NEW && (
            <div className="space-y-2">
              <Label htmlFor="dashboard-name">New dashboard name</Label>
              <Input id="dashboard-name" value={name} maxLength={100} required onChange={(e) => setName(e.target.value)} />
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" variant="brand" disabled={busy || (target === NEW && !name.trim())}>
              {busy ? "Saving…" : "Save chart"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
