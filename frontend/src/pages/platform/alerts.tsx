import { useEffect, useState } from "react";
import { BellRing, CheckCircle2, CircleHelp, Pencil, Plus, Trash2 } from "lucide-react";
import {
  AGGREGATION_LABELS,
  ALERT_SCOPE_LABELS,
  type Aggregation,
  type AlertIncident,
  type AlertRuleInput,
  type AlertRuleRow,
  type AlertScope,
  type AlertState,
  type AlertTargetState,
  COMPARISON_LABELS,
  type Comparison,
  RULE_AGGREGATIONS,
  useAlertPreview,
  useAlertRules,
  useCreateAlertRule,
  useDeleteAlertRule,
  useIncidents,
  useMetricRegistry,
  useUpdateAlertRule,
} from "@/api/telemetry";
import { PageHeader } from "@/components/layout/page-header";
import { TimeSeriesChart } from "@/components/time-series-chart";
import { ACTIONS_HEAD_CLASS, RowActions } from "@/components/data-table/row-actions";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
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
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { extractErrorEnvelope } from "@/lib/api";
import { useConfirm } from "@/lib/confirm";
import { isPlatformOwner, usePlatformAuth } from "@/lib/platform-auth";
import { toast } from "@/lib/use-toast";
import { LabelFilters, toChartSeries } from "./metrics";
import { formatFor } from "./observability";
import { FilterSelect, entries, shortTime } from "./traces";

const STATE_LABELS: Record<AlertState, string> = { firing: "Firing", no_data: "No data", ok: "OK" };
const STATE_ICONS = { firing: BellRing, no_data: CircleHelp, ok: CheckCircle2 };
const STATE_VARIANTS = { firing: "destructive", no_data: "warning", ok: "secondary" } as const;
const PREVIEW_DELAY_MS = 500;

function StateBadge({ state, count }: { state: AlertState; count?: number }) {
  const Icon = STATE_ICONS[state];
  return (
    <Badge variant={STATE_VARIANTS[state]}>
      <Icon aria-hidden />
      {count !== undefined ? `${count} ${STATE_LABELS[state].toLowerCase()}` : STATE_LABELS[state]}
    </Badge>
  );
}

function targetName(school: AlertTargetState["school"]) {
  return school?.name ?? "Platform";
}

/** "2 firing · 1 no data · 40 OK", with the firing targets named. */
function StatesSummary({ rule }: { rule: AlertRuleRow }) {
  if (!rule.enabled) return <span className="text-[13px] text-muted-foreground">Off</span>;
  if (!rule.states.length) return <span className="text-[13px] text-muted-foreground">Waiting for the first check</span>;
  const by = (state: AlertState) => rule.states.filter((s) => s.state === state);
  const firing = by("firing");
  return (
    <div className="space-y-1">
      <span className="inline-flex flex-wrap gap-2">
        {(["firing", "no_data", "ok"] as const).map((state) =>
          by(state).length ? <StateBadge key={state} state={state} count={by(state).length} /> : null,
        )}
      </span>
      {firing.length > 0 && (
        <p className="text-[12px] text-muted-foreground">
          {firing
            .slice(0, 3)
            .map((s) => targetName(s.school))
            .join(", ")}
          {firing.length > 3 ? ` and ${firing.length - 3} more` : ""}
        </p>
      )}
    </div>
  );
}

/**
 * Threshold alert rules on any metric, per school or for the platform, and
 * their incidents (spec 0006, AC-17 to AC-19). Every admin sees them; only
 * Owners change them. A rule emails once when it opens and once when it
 * resolves; missing data never does either.
 */
export default function ConsoleAlertsPage() {
  const { admin } = usePlatformAuth();
  const owner = isPlatformOwner(admin);
  const rules = useAlertRules();
  const update = useUpdateAlertRule();
  const remove = useDeleteAlertRule();
  const confirm = useConfirm();
  // undefined: closed, null: a new rule, a row: editing it.
  const [editing, setEditing] = useState<AlertRuleRow | null | undefined>(undefined);

  const toggle = async (rule: AlertRuleRow, enabled: boolean) => {
    try {
      await update.mutateAsync({ id: rule.id, enabled });
      toast({ title: `${rule.name} ${enabled ? "switched on" : "switched off"}` });
    } catch (err) {
      toast({ title: extractErrorEnvelope(err).detail, variant: "destructive" });
    }
  };

  const destroy = async (rule: AlertRuleRow) => {
    const ok = await confirm({
      title: `Delete ${rule.name}?`,
      description: "Its open incidents close without an email, and its history goes with it.",
      confirmLabel: "Delete rule",
      destructive: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(rule.id);
      toast({ title: `${rule.name} deleted` });
    } catch (err) {
      toast({ title: extractErrorEnvelope(err).detail, variant: "destructive" });
    }
  };

  return (
    <div className="space-y-10">
      <PageHeader
        section="Console · Observability"
        title="Alerts"
        description="Rules check every minute. One email when a rule holds for its whole window, and one more when it has been clear for 5 checks in a row."
        actions={
          owner && (
            <Button variant="brand" onClick={() => setEditing(null)}>
              <Plus data-icon="inline-start" />
              New rule
            </Button>
          )
        }
      />

      <section className="space-y-4" aria-label="Rules">
        <h2 className="font-serif text-[1.35rem] leading-tight tracking-[-0.01em]">Rules</h2>
        {rules.error ? (
          <Alert variant="destructive">
            <AlertDescription>{extractErrorEnvelope(rules.error).detail}</AlertDescription>
          </Alert>
        ) : rules.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : !rules.data?.length ? (
          <p className="py-6 text-center text-[13px] text-muted-foreground">
            No alert rules yet.{owner ? " Create one to hear about a struggling school before it calls." : ""}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Rule</TableHead>
                <TableHead>Scope</TableHead>
                <TableHead>Now</TableHead>
                <TableHead>On</TableHead>
                {owner && (
                  <TableHead className={ACTIONS_HEAD_CLASS}>
                    <span className="sr-only">Actions</span>
                  </TableHead>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rules.data.map((rule) => (
                <TableRow key={rule.id}>
                  <TableCell className="max-w-[360px] whitespace-normal">
                    <span className="block font-medium">{rule.name}</span>
                    <span className="block font-mono text-[12px] text-muted-foreground">{rule.description}</span>
                  </TableCell>
                  <TableCell className="text-[13px]">{ALERT_SCOPE_LABELS[rule.scope]}</TableCell>
                  <TableCell>
                    <StatesSummary rule={rule} />
                  </TableCell>
                  <TableCell>
                    {owner ? (
                      <Switch
                        checked={rule.enabled}
                        onCheckedChange={(on) => void toggle(rule, on)}
                        aria-label={`${rule.name} is ${rule.enabled ? "on" : "off"}`}
                      />
                    ) : (
                      <span className="text-[13px]">{rule.enabled ? "On" : "Off"}</span>
                    )}
                  </TableCell>
                  {owner && (
                    <TableCell>
                      <RowActions>
                        <Button variant="ghost" size="sm" onClick={() => setEditing(rule)} aria-label={`Edit ${rule.name}`}>
                          <Pencil data-icon="inline-start" />
                          Edit
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => void destroy(rule)} aria-label={`Delete ${rule.name}`}>
                          <Trash2 data-icon="inline-start" />
                          Delete
                        </Button>
                      </RowActions>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <IncidentsSection />

      <RuleDialog rule={editing} onClose={() => setEditing(undefined)} />
    </div>
  );
}

function IncidentsSection() {
  // Cursors of the pages seen so far, to page back.
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const cursor = cursors[cursors.length - 1];
  const incidents = useIncidents(cursor);
  const rows = incidents.data?.results ?? [];

  return (
    <section className="space-y-4" aria-label="Incidents">
      <h2 className="font-serif text-[1.35rem] leading-tight tracking-[-0.01em]">Incidents</h2>
      {incidents.error ? (
        <Alert variant="destructive">
          <AlertDescription>{extractErrorEnvelope(incidents.error).detail}</AlertDescription>
        </Alert>
      ) : incidents.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : !rows.length ? (
        <p className="py-6 text-center text-[13px] text-muted-foreground">No incidents yet.</p>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Rule</TableHead>
                <TableHead>School</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Opened</TableHead>
                <TableHead className="text-right">Worst</TableHead>
                <TableHead className="text-right">Latest</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((incident) => (
                <IncidentRow key={incident.id} incident={incident} />
              ))}
            </TableBody>
          </Table>
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" disabled={cursors.length < 2} onClick={() => setCursors((c) => c.slice(0, -1))}>
              Newer
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!incidents.data?.next}
              onClick={() => incidents.data?.next && setCursors((c) => [...c, incidents.data.next])}
            >
              Older
            </Button>
          </div>
        </>
      )}
    </section>
  );
}

function IncidentRow({ incident }: { incident: AlertIncident }) {
  const format = formatFor(incident.unit, "last");
  const open = incident.resolved_at === null;
  return (
    <TableRow>
      <TableCell>{incident.rule.name}</TableCell>
      <TableCell>{incident.school?.name ?? "Platform"}</TableCell>
      <TableCell>
        {open ? (
          <StateBadge state="firing" />
        ) : (
          <span className="text-[13px] text-muted-foreground">Resolved {shortTime(incident.resolved_at!)}</span>
        )}
      </TableCell>
      <TableCell className="text-[13px]">{shortTime(incident.started_at)}</TableCell>
      <TableCell className="text-right tabular-nums">{format(incident.peak_value)}</TableCell>
      <TableCell className="text-right tabular-nums">{format(incident.last_value)}</TableCell>
    </TableRow>
  );
}

interface Draft {
  name: string;
  metric_name: string;
  aggregation: Aggregation;
  filters: Record<string, string>;
  comparison: Comparison;
  threshold: string;
  for_minutes: string;
  scope: AlertScope;
  enabled: boolean;
}

const FIELDS = ["name", "metric_name", "aggregation", "filters", "comparison", "threshold", "for_minutes", "scope"] as const;

function toDraft(rule: AlertRuleRow | null): Draft {
  return {
    name: rule?.name ?? "",
    metric_name: rule?.metric_name ?? "http.server.duration",
    aggregation: rule?.aggregation ?? "p95",
    filters: rule?.filters ?? {},
    comparison: rule?.comparison ?? "gt",
    threshold: rule ? String(rule.threshold) : "2000",
    for_minutes: rule ? String(rule.for_minutes) : "10",
    scope: rule?.scope ?? "per_school",
    enabled: rule?.enabled ?? true,
  };
}

/** The rule a draft makes, or null while a number is missing or out of range. */
function toInput(d: Draft): AlertRuleInput | null {
  const threshold = Number(d.threshold);
  const minutes = Number(d.for_minutes);
  if (d.threshold.trim() === "" || !Number.isFinite(threshold)) return null;
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > 120) return null;
  return { ...d, name: d.name.trim(), threshold, for_minutes: minutes };
}

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  const key = JSON.stringify(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(timer);
    // The serialised value is the dependency, so a new object with the same
    // content doesn't restart the wait.
  }, [key, ms]);
  return settled;
}

function RuleDialog({ rule, onClose }: { rule: AlertRuleRow | null | undefined; onClose: () => void }) {
  const registry = useMetricRegistry();
  const create = useCreateAlertRule();
  const update = useUpdateAlertRule();
  const open = rule !== undefined;
  const editing = rule ?? null;
  const [draft, setDraft] = useState<Draft>(toDraft(null));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setDraft(toDraft(editing));
      setErrors({});
      setError(null);
    }
  }, [open, editing]);

  const metric = registry.data?.find((m) => m.name === draft.metric_name);
  const aggregations = metric ? RULE_AGGREGATIONS[metric.type] : [];
  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));

  const pickMetric = (name: string | undefined) => {
    const next = registry.data?.find((m) => m.name === name);
    if (!next) return;
    const allowed = RULE_AGGREGATIONS[next.type];
    set({
      metric_name: next.name,
      filters: {},
      aggregation: allowed.includes(draft.aggregation) ? draft.aggregation : allowed[0],
      scope: next.scope === "platform" ? "platform" : draft.scope,
    });
  };

  const input = toInput(draft);
  let previewInput: Omit<AlertRuleInput, "name" | "enabled"> | null = null;
  if (open && input && metric) {
    const { name: _name, enabled: _enabled, ...rest } = input;
    previewInput = rest;
  }
  const preview = useAlertPreview(useDebounced(previewInput, PREVIEW_DELAY_MS));

  const save = async () => {
    setError(null);
    setErrors({});
    if (!input) {
      setError("Give a threshold, and a window of 1 to 120 minutes.");
      return;
    }
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, ...input });
        toast({ title: `${input.name} saved` });
      } else {
        await create.mutateAsync(input);
        toast({ title: `${input.name} created` });
      }
      onClose();
    } catch (err) {
      const envelope = extractErrorEnvelope(err);
      const found = Object.fromEntries(
        FIELDS.filter((f) => envelope.errors?.[f]).map((f) => [f, envelope.errors![f].join(" ")]),
      );
      setErrors(found);
      if (!Object.keys(found).length) setError(envelope.detail);
    }
  };

  const busy = create.isPending || update.isPending;
  const fieldError = (name: string) =>
    errors[name] ? <p className="text-[12px] text-destructive">{errors[name]}</p> : null;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{editing ? `Edit ${editing.name}` : "New alert rule"}</DialogTitle>
          <DialogDescription>
            {editing
              ? "Changing anything but the name closes its open incidents without an email, and checks start over."
              : "The rule fires when every minute of its window breaks the threshold. Gaps in the data never fire or resolve it."}
          </DialogDescription>
        </DialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <form
          className="space-y-4"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="rule-name">Name</Label>
              <Input
                id="rule-name"
                autoFocus
                placeholder="Slow school"
                maxLength={100}
                value={draft.name}
                onChange={(e) => set({ name: e.target.value })}
              />
              {fieldError("name")}
            </div>
            <div className="space-y-2">
              <Label>Metric</Label>
              <FilterSelect
                label="Metric"
                value={draft.metric_name}
                options={(registry.data ?? []).map((m) => [m.name, m.name] as [string, string])}
                onChange={pickMetric}
                anyLabel=""
                width="w-full"
              />
              {metric?.description && <p className="text-[12px] text-muted-foreground">{metric.description}</p>}
              {fieldError("metric_name")}
            </div>
            <div className="space-y-2">
              <Label>Aggregation</Label>
              <FilterSelect
                label="Aggregation"
                value={draft.aggregation}
                options={aggregations.map((a) => [a, AGGREGATION_LABELS[a]] as [Aggregation, string])}
                onChange={(v) => v && set({ aggregation: v })}
                anyLabel=""
                width="w-full"
              />
              {fieldError("aggregation")}
            </div>
            {metric && Object.keys(metric.labels).length > 0 && (
              <div className="space-y-2 sm:col-span-2">
                <Label>Only count</Label>
                <div className="flex flex-wrap gap-2">
                  <LabelFilters metric={metric} value={draft.filters} onChange={(filters) => set({ filters })} />
                </div>
                {fieldError("filters")}
              </div>
            )}
            <div className="space-y-2">
              <Label>Fires when the value is</Label>
              <div className="flex gap-2">
                <FilterSelect
                  label="Comparison"
                  value={draft.comparison}
                  options={entries(COMPARISON_LABELS)}
                  onChange={(v) => v && set({ comparison: v })}
                  anyLabel=""
                  width="w-[110px]"
                />
                <Input
                  aria-label="Threshold"
                  inputMode="decimal"
                  className="h-8"
                  value={draft.threshold}
                  onChange={(e) => set({ threshold: e.target.value })}
                />
                {metric && metric.unit !== "1" && (
                  <span className="self-center font-mono text-[12px] text-muted-foreground">{metric.unit}</span>
                )}
              </div>
              {fieldError("threshold")}
              {fieldError("comparison")}
            </div>
            <div className="space-y-2">
              <Label htmlFor="rule-minutes">For every minute of the last</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="rule-minutes"
                  inputMode="numeric"
                  className="h-8 w-24"
                  value={draft.for_minutes}
                  onChange={(e) => set({ for_minutes: e.target.value })}
                />
                <span className="text-[13px] text-muted-foreground">minutes (1 to 120)</span>
              </div>
              {fieldError("for_minutes")}
            </div>
            <div className="space-y-2">
              <Label>Check</Label>
              <FilterSelect
                label="Scope"
                value={draft.scope}
                options={entries(ALERT_SCOPE_LABELS).filter(([s]) => s === "platform" || metric?.scope !== "platform")}
                onChange={(v) => v && set({ scope: v })}
                anyLabel=""
                width="w-full"
              />
              {metric?.scope === "platform" && (
                <p className="text-[12px] text-muted-foreground">This metric has no per school values.</p>
              )}
              {fieldError("scope")}
            </div>
            <div className="flex items-center justify-between gap-4 self-end border-y border-[color:var(--border)] py-3">
              <Label htmlFor="rule-enabled">On</Label>
              <Switch id="rule-enabled" checked={draft.enabled} onCheckedChange={(enabled) => set({ enabled })} />
            </div>
          </div>

          <RulePreview preview={preview} threshold={input?.threshold} unit={metric?.unit ?? "1"} aggregation={draft.aggregation} />

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" variant="brand" disabled={busy || !draft.name.trim()}>
              {busy ? "Saving…" : editing ? "Save rule" : "Create rule"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RulePreview({
  preview,
  threshold,
  unit,
  aggregation,
}: {
  preview: ReturnType<typeof useAlertPreview>;
  threshold: number | undefined;
  unit: string;
  aggregation: Aggregation;
}) {
  const format = formatFor(unit, aggregation);
  const data = preview.data;
  const series = toChartSeries(data, "Value");
  if (data && threshold !== undefined && series.length) {
    series.push({ label: "Threshold", points: series[0].points.map(([t]) => [t, threshold]) });
  }
  const name = (school: number | null) => (school === null ? "Platform" : (data?.schools[String(school)]?.name ?? `School ${school}`));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-[14px]">The last 6 hours</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {preview.error ? (
          <Alert variant="destructive">
            <AlertDescription>{extractErrorEnvelope(preview.error).detail}</AlertDescription>
          </Alert>
        ) : !data ? (
          <Skeleton className="h-[160px] w-full" />
        ) : (
          <>
            <TimeSeriesChart
              series={series}
              label="Rule preview"
              format={format}
              height={160}
              dropped={data.dropped}
              step={data.step}
            />
            <p className="text-[13px]">
              {data.fired.length === 0
                ? "It would not have fired."
                : `It would have fired ${data.fired.length} time${data.fired.length === 1 ? "" : "s"}: ` +
                  data.fired
                    .slice(0, 5)
                    .map((f) => `${name(f.school)} at ${new Date(f.minute * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`)
                    .join(", ") +
                  (data.fired.length > 5 ? `, and ${data.fired.length - 5} more` : "") +
                  "."}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
