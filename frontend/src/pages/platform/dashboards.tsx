import { useEffect, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, LineChart, Pencil, Plus, School, Trash2 } from "lucide-react";
import {
  type DashboardDetail,
  METRIC_RANGE_LABELS,
  type MetricRange,
  type SavedChart,
  chartInput,
  useCreateDashboard,
  useDashboard,
  useDashboards,
  useDeleteDashboard,
  useMetricQuery,
  useMetricRegistry,
  useSchoolOptions,
  useUpdateDashboard,
} from "@/api/telemetry";
import { ListShell } from "@/components/data-table/list-shell";
import { PageHeader } from "@/components/layout/page-header";
import { TimeSeriesChart } from "@/components/time-series-chart";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
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
import { useConfirm } from "@/lib/confirm";
import { isPlatformOwner, usePlatformAuth } from "@/lib/platform-auth";
import { toast } from "@/lib/use-toast";
import { toChartSeries } from "./metrics";
import { formatFor } from "./observability";
import { FilterSelect, entries } from "./traces";

export interface DashboardSearch {
  range?: MetricRange;
  school?: number;
}

function when(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function byline(d: Pick<DashboardDetail, "created_by" | "updated_by" | "updated_at">) {
  const made = d.created_by ? `Created by ${d.created_by.name}` : "Created by a removed admin";
  const edited = d.updated_by ? `last edited by ${d.updated_by.name}` : "last edited";
  return `${made} · ${edited}, ${when(d.updated_at)}`;
}

/** Shared dashboards of saved metric charts (spec 0006, AC-24). */
export default function ConsoleDashboardsPage() {
  const list = useDashboards();
  const [creating, setCreating] = useState(false);

  return (
    <>
      <ListShell
        title="Dashboards"
        description="Charts saved from the metric explorer, shared with every admin. Each dashboard has its own school picker and time range."
        toolbar={
          <Button variant="brand" onClick={() => setCreating(true)}>
            <Plus data-icon="inline-start" />
            New dashboard
          </Button>
        }
        isLoading={list.isLoading}
        error={list.error}
        isEmpty={!list.data?.length}
        emptyMessage="No dashboards yet. Save a chart from the metric explorer to start one."
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Dashboard</TableHead>
              <TableHead className="text-right">Charts</TableHead>
              <TableHead>Created by</TableHead>
              <TableHead>Last edited</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.map((d) => (
              <TableRow key={d.id}>
                <TableCell>
                  <Link
                    to="/console/observability/dashboards/$id"
                    params={{ id: String(d.id) }}
                    className="font-medium hover:underline"
                  >
                    {d.name}
                  </Link>
                </TableCell>
                <TableCell className="text-right tabular-nums">{d.chart_count}</TableCell>
                <TableCell>{d.created_by?.name ?? "Removed admin"}</TableCell>
                <TableCell className="text-[13px]">
                  {when(d.updated_at)}
                  {d.updated_by && <span className="text-muted-foreground"> by {d.updated_by.name}</span>}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </ListShell>
      <NewDashboardDialog open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

function NewDashboardDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const create = useCreateDashboard();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setName("");
      setError(null);
    }
  }, [open]);

  const save = async () => {
    try {
      const dashboard = await create.mutateAsync({ name: name.trim(), charts: [] });
      onClose();
      void navigate({ to: "/console/observability/dashboards/$id", params: { id: String(dashboard.id) } });
    } catch (err) {
      setError(extractErrorEnvelope(err).detail);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New dashboard</DialogTitle>
          <DialogDescription>Add charts to it from the metric explorer.</DialogDescription>
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
            <Label htmlFor="new-dashboard-name">Name</Label>
            <Input id="new-dashboard-name" autoFocus value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" variant="brand" disabled={create.isPending || !name.trim()}>
              {create.isPending ? "Creating…" : "Create dashboard"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ChartCard({
  chart,
  range,
  school,
  editing,
}: {
  chart: SavedChart;
  range: MetricRange;
  school?: number;
  editing?: { onUp?: () => void; onDown?: () => void; onRemove: () => void; onTitle: (title: string) => void };
}) {
  const registry = useMetricRegistry();
  const metric = registry.data?.find((m) => m.name === chart.metric_name);
  const result = useMetricQuery(
    editing
      ? null
      : {
          metric: chart.metric_name,
          aggregation: chart.aggregation,
          group_by: chart.group_by || undefined,
          filters: chart.filters,
          // A fixed school wins over the page's picker.
          school: chart.school?.id ?? school,
        },
    range,
  );
  const format = formatFor(metric?.unit ?? "1", chart.aggregation);

  return (
    <Card>
      <CardHeader className="space-y-2">
        {editing ? (
          <div className="flex items-center gap-2">
            <Input
              aria-label="Chart title"
              className="h-8 text-[13px]"
              value={chart.title}
              maxLength={100}
              onChange={(e) => editing.onTitle(e.target.value)}
            />
            <Button variant="ghost" size="sm" disabled={!editing.onUp} onClick={editing.onUp} aria-label={`Move ${chart.title} up`}>
              <ArrowUp />
            </Button>
            <Button variant="ghost" size="sm" disabled={!editing.onDown} onClick={editing.onDown} aria-label={`Move ${chart.title} down`}>
              <ArrowDown />
            </Button>
            <Button variant="ghost" size="sm" onClick={editing.onRemove} aria-label={`Remove ${chart.title}`}>
              <Trash2 />
            </Button>
          </div>
        ) : (
          <CardTitle className="text-[14px]">{chart.title}</CardTitle>
        )}
        <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
          <span className="font-mono">
            {chart.aggregation} of {chart.metric_name}
            {chart.group_by ? ` by ${chart.group_by}` : ""}
          </span>
          {Object.entries(chart.filters).map(([k, v]) => (
            <span key={k} className="font-mono">
              {k}={v}
            </span>
          ))}
          {chart.school && (
            <Badge variant={chart.school.purged ? "warning" : "secondary"}>
              <School aria-hidden />
              {chart.school.purged ? `${chart.school.name}: school purged` : chart.school.name}
            </Badge>
          )}
        </div>
      </CardHeader>
      {!editing && (
        <CardContent>
          {result.error ? (
            <Alert variant="destructive">
              <AlertDescription>{extractErrorEnvelope(result.error).detail}</AlertDescription>
            </Alert>
          ) : !result.data ? (
            <Skeleton className="h-[180px] w-full" />
          ) : (
            <TimeSeriesChart
              series={toChartSeries(result.data, chart.title)}
              label={chart.title}
              format={format}
              dropped={result.data.dropped}
              step={result.data.step}
            />
          )}
        </CardContent>
      )}
    </Card>
  );
}

/** One shared dashboard, under the page's time range and school picker. */
export function ConsoleDashboardPage({ id, search }: { id: number; search: DashboardSearch }) {
  const navigate = useNavigate();
  const confirm = useConfirm();
  const { admin } = usePlatformAuth();
  const owner = isPlatformOwner(admin);
  const dashboard = useDashboard(id);
  const schools = useSchoolOptions();
  const update = useUpdateDashboard();
  const remove = useDeleteDashboard();
  const range = search.range ?? "24h";
  // Null when not editing: the draft name and chart order.
  const [draft, setDraft] = useState<{ name: string; charts: SavedChart[] } | null>(null);

  const setSearch = (patch: DashboardSearch) =>
    void navigate({
      to: "/console/observability/dashboards/$id",
      params: { id: String(id) },
      search: { ...search, ...patch },
      replace: true,
    });

  const schoolOptions: Array<[string, string]> = (schools.data ?? [])
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((s) => [String(s.id), s.name]);

  if (dashboard.error) {
    return (
      <div className="space-y-6">
        <Alert variant="destructive">
          <AlertDescription>{extractErrorEnvelope(dashboard.error).detail}</AlertDescription>
        </Alert>
        <Link to="/console/observability/dashboards" className={buttonVariants({ variant: "outline", size: "sm" })}>
          All dashboards
        </Link>
      </div>
    );
  }
  const data = dashboard.data;
  if (!data) return <Skeleton className="h-40 w-full" />;

  const charts = draft?.charts ?? data.charts;
  const move = (from: number, to: number) =>
    setDraft((d) => {
      if (!d) return d;
      const next = d.charts.slice();
      const [chart] = next.splice(from, 1);
      next.splice(to, 0, chart);
      return { ...d, charts: next };
    });

  const save = async () => {
    if (!draft) return;
    try {
      await update.mutateAsync({
        id,
        name: draft.name.trim() || data.name,
        charts: draft.charts.map((c) => chartInput({ ...c, title: c.title.trim() || `${c.aggregation} of ${c.metric_name}` })),
      });
      setDraft(null);
      toast({ title: "Dashboard saved" });
    } catch (err) {
      const envelope = extractErrorEnvelope(err);
      toast({ title: "Couldn't save the dashboard", description: envelope.detail, variant: "destructive" });
    }
  };

  const destroy = async () => {
    const ok = await confirm({
      title: `Delete ${data.name}?`,
      description: "Its charts go with it, for every admin. The metrics themselves are kept.",
      confirmLabel: "Delete dashboard",
      destructive: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(id);
      toast({ title: `${data.name} deleted` });
      void navigate({ to: "/console/observability/dashboards" });
    } catch (err) {
      toast({ title: "Couldn't delete the dashboard", description: extractErrorEnvelope(err).detail, variant: "destructive" });
    }
  };

  return (
    <div className="space-y-8">
      <PageHeader
        section="Console · Dashboards"
        title={data.name}
        description={byline(data)}
        actions={
          draft ? (
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setDraft(null)}>
                Cancel
              </Button>
              <Button variant="brand" onClick={() => void save()} disabled={update.isPending}>
                {update.isPending ? "Saving…" : "Save changes"}
              </Button>
            </div>
          ) : (
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setDraft({ name: data.name, charts: data.charts })}>
                <Pencil data-icon="inline-start" />
                Edit
              </Button>
              {owner && (
                <Button variant="outline" onClick={() => void destroy()}>
                  <Trash2 data-icon="inline-start" />
                  Delete
                </Button>
              )}
            </div>
          )
        }
      />

      {draft ? (
        <div className="max-w-md space-y-2">
          <Label htmlFor="dashboard-name">Name</Label>
          <Input
            id="dashboard-name"
            value={draft.name}
            maxLength={100}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
          <FilterSelect
            label="Range"
            value={range}
            options={entries(METRIC_RANGE_LABELS)}
            onChange={(v) => setSearch({ range: v === "24h" ? undefined : v })}
            anyLabel=""
          />
          <FilterSelect
            label="School"
            value={search.school === undefined ? undefined : String(search.school)}
            options={schoolOptions}
            onChange={(v) => setSearch({ school: v ? Number(v) : undefined })}
            anyLabel="All schools"
            width="w-[200px]"
          />
          <Link to="/console/observability/metrics" className={buttonVariants({ variant: "ghost", size: "sm" })}>
            <LineChart data-icon="inline-start" />
            Add a chart from the explorer
          </Link>
        </div>
      )}

      {charts.length === 0 ? (
        <p className="py-10 text-center text-[13px] text-muted-foreground">
          No charts here yet. Open the metric explorer, pick a chart and choose &ldquo;Save to dashboard&rdquo;.
        </p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {charts.map((chart, i) => (
            <ChartCard
              key={chart.id}
              chart={chart}
              range={range}
              school={search.school}
              editing={
                draft
                  ? {
                      onUp: i > 0 ? () => move(i, i - 1) : undefined,
                      onDown: i < charts.length - 1 ? () => move(i, i + 1) : undefined,
                      onRemove: () => setDraft({ ...draft, charts: draft.charts.filter((c) => c.id !== chart.id) }),
                      onTitle: (title) =>
                        setDraft({ ...draft, charts: draft.charts.map((c) => (c.id === chart.id ? { ...c, title } : c)) }),
                    }
                  : undefined
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}
