import { useId } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, XAxis, YAxis } from "recharts";

import { type ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";

function shortDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function longDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

function hourStamp(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export interface TrendPoint {
  date: string;
  value: number;
  /** Extra lines for the tooltip, e.g. staff and jobs that day. */
  details?: Array<[string, number]>;
}

/**
 * One series over time on shadcn charts: a 2px line on a soft area,
 * recessive gridlines, and a crosshair with a tooltip on hover. ``limit``
 * draws a dashed line for a plan's cap. The same numbers sit in a visually
 * hidden table.
 */
export function TrendChart({
  points,
  label,
  limit,
  unit = "day",
  empty = "The trend fills in as nightly usage snapshots are taken.",
}: {
  points: TrendPoint[];
  label: string;
  limit?: number | null;
  /** One point per day (the default) or per hour. */
  unit?: "day" | "hour";
  empty?: string;
}) {
  const fillId = `trend-fill-${useId().replace(/:/g, "")}`;
  if (points.length < 2) {
    return <p className="px-5 py-8 text-center text-[13px] text-muted-foreground">{empty}</p>;
  }

  const max = Math.max(...points.map((p) => p.value), limit ?? 0, 1);
  // A rounded top so gridlines land on readable numbers.
  const magnitude = 10 ** Math.floor(Math.log10(max));
  const top = Math.ceil(max / magnitude) * magnitude;
  const last = points.length - 1;
  const stamp = unit === "hour" ? hourStamp : longDate;
  const config = { value: { label, color: "var(--brand-strong)" } } satisfies ChartConfig;

  return (
    <div className="px-5 pt-4 pb-3">
      <ChartContainer
        config={config}
        className="aspect-auto h-[204px] w-full [&_.recharts-cartesian-axis-tick_text]:font-mono [&_.recharts-cartesian-axis-tick_text]:text-[10px]"
        role="img"
        aria-label={`${label}, ${shortDate(points[0].date)} to ${shortDate(points[last].date)}`}
      >
        <AreaChart data={points} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--brand-soft)" stopOpacity={0.9} />
              <stop offset="100%" stopColor="var(--brand-soft)" stopOpacity={0.3} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis
            dataKey="date"
            ticks={[points[0].date, points[last].date]}
            interval={0}
            tickFormatter={shortDate}
            tickLine={false}
            axisLine={false}
            tickMargin={8}
          />
          <YAxis
            domain={[0, top]}
            ticks={[0, top / 2, top]}
            tickFormatter={(v: number) => v.toLocaleString()}
            tickLine={false}
            axisLine={false}
            width={40}
          />
          {limit != null && (
            <ReferenceLine
              y={limit}
              stroke="var(--destructive)"
              strokeDasharray="5 4"
              label={{
                value: `Limit ${limit.toLocaleString()}`,
                position: "insideTopRight",
                fill: "var(--destructive)",
                fontSize: 10,
                className: "font-mono",
              }}
            />
          )}
          <ChartTooltip
            cursor={{ stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }}
            content={
              <ChartTooltipContent
                className="min-w-[150px]"
                labelClassName="font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-muted-foreground"
                labelFormatter={(_, payload) => stamp(String(payload[0]?.payload?.date))}
                formatter={(_, __, item) => {
                  const point = item.payload as TrendPoint;
                  return (
                    <div className="grid w-full gap-1">
                      {[[label, point.value] as [string, number], ...(point.details ?? [])].map(([name, value], i) => (
                        <div key={name} className="flex justify-between gap-4">
                          <span className="text-muted-foreground">{name}</span>
                          <span className={i === 0 ? "font-mono font-medium tabular-nums" : "font-mono tabular-nums"}>
                            {value.toLocaleString()}
                          </span>
                        </div>
                      ))}
                    </div>
                  );
                }}
              />
            }
          />
          <Area
            dataKey="value"
            type="linear"
            fill={`url(#${fillId})`}
            stroke="var(--color-value)"
            strokeWidth={2}
            activeDot={{ r: 4, fill: "var(--color-value)", stroke: "var(--card)", strokeWidth: 2 }}
          />
        </AreaChart>
      </ChartContainer>
      {/* sr-only on a wrapper: a table never shrinks below its rows, so
          on the table itself it stays full height and adds blank scroll. */}
      <div className="sr-only">
        <table>
          <caption>
            {label} by {unit}
          </caption>
          <thead>
            <tr>
              <th>{unit === "hour" ? "Hour" : "Date"}</th>
              <th>{label}</th>
              {points[0].details?.map(([name]) => <th key={name}>{name}</th>)}
            </tr>
          </thead>
          <tbody>
            {points.map((p) => (
              <tr key={p.date}>
                <td>{p.date}</td>
                <td>{p.value}</td>
                {p.details?.map(([name, value]) => <td key={name}>{value}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
