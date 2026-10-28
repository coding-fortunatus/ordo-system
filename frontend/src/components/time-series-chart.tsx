import { useMemo } from "react";
import { CartesianGrid, Line, LineChart, ReferenceArea, XAxis, YAxis } from "recharts";

import {
  type ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";

/** One line: ``[epoch seconds, value]`` pairs; null is missing (drawn as a gap). */
export interface ChartSeries {
  label: string;
  points: Array<[number, number | null]>;
}

// The design system's chart tokens are one hue in five steps, so series are
// told apart by the two steps that read in both themes plus a dash pattern,
// and always by the legend.
const STROKES = ["var(--primary)", "var(--chart-2)"];
const DASHES = ["", "", "6 4", "6 4", "2 3", "2 3", "10 3 2 3", "10 3 2 3"];
const OTHER = "other";
const NO_DROPS: number[] = [];

function style(index: number, label: string) {
  if (label === OTHER) return { stroke: "var(--muted-foreground)", dash: "2 3" };
  return { stroke: STROKES[index % STROKES.length], dash: DASHES[index % DASHES.length] };
}

/** A short stretch of the series' line, dash and all, for the legend and tooltip. */
function Swatch({ stroke, dash }: { stroke: string; dash: string }) {
  return (
    <svg aria-hidden width={16} height={8} className="shrink-0">
      <line x1={0} x2={16} y1={4} y2={4} stroke={stroke} strokeWidth={2} strokeDasharray={dash} />
    </svg>
  );
}

function tickTime(seconds: number, span: number) {
  const date = new Date(seconds * 1000);
  return span > 2 * 86400
    ? date.toLocaleDateString(undefined, { day: "numeric", month: "short" })
    : date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/**
 * Lines over time on shadcn charts: missing points are gaps, zeros sit on
 * the axis, and hovering shows every series' value in the tooltip. Buckets
 * in which telemetry was dropped are shaded (spec 0006, AC-4). Colors are
 * theme tokens, so they follow a theme change. The same numbers sit in a
 * visually hidden table.
 */
export function TimeSeriesChart({
  series,
  label,
  format,
  height = 180,
  dropped = NO_DROPS,
  step = 60,
}: {
  series: ChartSeries[];
  /** Names the chart for screen readers. */
  label: string;
  format: (value: number) => string;
  height?: number;
  /** Bucket starts (epoch seconds) in which some telemetry was dropped. */
  dropped?: number[];
  /** Bucket width in seconds. */
  step?: number;
}) {
  const xs = series[0]?.points.map(([t]) => t) ?? [];
  const shown = dropped.filter((t) => xs.length > 0 && t >= xs[0] && t <= xs[xs.length - 1]);
  const span = xs.length ? xs[xs.length - 1] - xs[0] : 0;

  // Series keys are positional (``s0``, ``s1``…) so labels never have to be
  // valid CSS variable names.
  const { data, config } = useMemo(() => {
    const config: ChartConfig = {};
    series.forEach((s, i) => {
      config[`s${i}`] = { label: s.label, color: style(i, s.label).stroke };
    });
    const data = (series[0]?.points ?? []).map(([t], row) => {
      const datum: Record<string, number | null> = { t };
      series.forEach((s, i) => {
        datum[`s${i}`] = s.points[row]?.[1] ?? null;
      });
      return datum;
    });
    return { data, config };
  }, [series]);

  if (!series.length) {
    return <p className="px-5 py-8 text-center text-[13px] text-muted-foreground">No data in this range.</p>;
  }

  return (
    <figure className="m-0">
      <ChartContainer
        config={config}
        className="aspect-auto w-full text-[12px] [&_.recharts-cartesian-axis-tick_text]:font-mono [&_.recharts-cartesian-axis-tick_text]:text-[10px]"
        style={{ height: height + 32 }}
        role="img"
        aria-label={label}
      >
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          {shown.map((t) => (
            <ReferenceArea
              key={t}
              x1={t}
              x2={t + step}
              fill="var(--muted)"
              fillOpacity={1}
              ifOverflow="hidden"
            />
          ))}
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(t: number) => tickTime(t, span)}
            tickLine={false}
            axisLine={false}
            tickMargin={8}
            minTickGap={32}
          />
          <YAxis
            domain={([min, max]: readonly number[]) => [Math.min(0, min), max > 0 ? max * 1.1 : 1]}
            tickFormatter={(v: number) => format(v)}
            tickLine={false}
            axisLine={false}
            width={64}
          />
          <ChartTooltip
            cursor={{ stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }}
            content={
              <ChartTooltipContent
                labelFormatter={(_, payload) => new Date(Number(payload[0]?.payload?.t) * 1000).toLocaleString()}
                formatter={(value, _, item) => {
                  const i = Number(String(item.dataKey).slice(1));
                  return (
                    <div className="flex w-full items-center gap-2">
                      <Swatch {...style(i, series[i].label)} />
                      <span className="text-muted-foreground">{series[i].label}</span>
                      <span className="ml-auto font-mono font-medium tabular-nums">
                        {value == null ? "no data" : format(Number(value))}
                      </span>
                    </div>
                  );
                }}
              />
            }
          />
          <ChartLegend
            content={() => (
              <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 pt-3">
                {series.map((s, i) => (
                  <div key={s.label} className="flex items-center gap-1.5">
                    <Swatch {...style(i, s.label)} />
                    {s.label}
                  </div>
                ))}
              </div>
            )}
          />
          {series.map((s, i) => (
            <Line
              key={s.label}
              dataKey={`s${i}`}
              name={s.label}
              type="linear"
              stroke={`var(--color-s${i})`}
              strokeWidth={2}
              strokeDasharray={style(i, s.label).dash}
              dot={false}
              activeDot={{ r: 4 }}
              connectNulls={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ChartContainer>
      {shown.length > 0 && (
        <figcaption className="mt-1 flex items-center gap-1.5 text-[12px] text-muted-foreground">
          <span aria-hidden className="inline-block size-2.5 rounded-[2px] bg-muted ring-1 ring-border" />
          Some telemetry was dropped here
        </figcaption>
      )}
      <table className="sr-only">
        <caption>
          {label}
          {shown.length > 0 && `. Some telemetry was dropped in ${shown.length} of these time steps.`}
        </caption>
        <thead>
          <tr>
            <th>Series</th>
            <th>Latest</th>
            <th>Highest</th>
          </tr>
        </thead>
        <tbody>
          {series.map((s) => {
            const values = s.points.map(([, v]) => v).filter((v): v is number => v != null);
            return (
              <tr key={s.label}>
                <td>{s.label}</td>
                <td>{values.length ? format(values[values.length - 1]) : "no data"}</td>
                <td>{values.length ? format(Math.max(...values)) : "no data"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </figure>
  );
}
