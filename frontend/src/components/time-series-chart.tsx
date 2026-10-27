import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

/** One line: ``[epoch seconds, value]`` pairs; null is missing (drawn as a gap). */
export interface ChartSeries {
  label: string;
  points: Array<[number, number | null]>;
}

// The design system's chart tokens are one hue in five steps, so series are
// told apart by the two steps that read in both themes plus a dash pattern,
// and always by the legend.
const STROKES = ["--primary", "--chart-2"];
const DASHES: number[][] = [[], [], [6, 4], [6, 4], [2, 3], [2, 3], [10, 3, 2, 3], [10, 3, 2, 3]];
const OTHER = "other";
const NO_DROPS: number[] = [];

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "currentColor";
}

function style(index: number, label: string) {
  if (label === OTHER) return { stroke: token("--muted-foreground"), dash: [2, 3] };
  return { stroke: token(STROKES[index % STROKES.length]), dash: DASHES[index % DASHES.length] };
}

function aligned(series: ChartSeries[]): uPlot.AlignedData {
  const xs = series[0]?.points.map(([t]) => t) ?? [];
  return [xs, ...series.map((s) => s.points.map(([, v]) => v))] as uPlot.AlignedData;
}

/** Shades each dropped bucket ``[t, t + step)`` behind the lines. */
function dropShading(dropped: number[], step: number): uPlot.Plugin {
  return {
    hooks: {
      drawClear: (u) => {
        if (!dropped.length) return;
        const { ctx, bbox } = u;
        ctx.save();
        ctx.fillStyle = token("--muted");
        for (const t of dropped) {
          const left = Math.max(u.valToPos(t, "x", true), bbox.left);
          const right = Math.min(u.valToPos(t + step, "x", true), bbox.left + bbox.width);
          if (right > left) ctx.fillRect(left, bbox.top, right - left, bbox.height);
        }
        ctx.restore();
      },
    },
  };
}

/**
 * Lines over time with uPlot: missing points are gaps, zeros sit on the
 * axis, and hovering shows every series' value in the legend. Buckets in
 * which telemetry was dropped are shaded (spec 0006, AC-4). Colors come
 * from the theme tokens and follow a theme change. The same numbers sit in
 * a visually hidden table.
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
  const box = useRef<HTMLDivElement>(null);
  const formatRef = useRef(format);
  formatRef.current = format;
  const xs = series[0]?.points.map(([t]) => t) ?? [];
  const shown = dropped.filter((t) => xs.length > 0 && t >= xs[0] && t <= xs[xs.length - 1]);

  useEffect(() => {
    const el = box.current;
    if (!el || !series.length) return;
    let plot: uPlot | null = null;

    const draw = () => {
      plot?.destroy();
      const axis = { stroke: token("--muted-foreground"), grid: { stroke: token("--border"), width: 1 }, ticks: { show: false } };
      const options: uPlot.Options = {
        width: el.clientWidth || 600,
        height,
        cursor: { y: false, points: { size: 8 } },
        plugins: [dropShading(dropped, step)],
        legend: { live: true },
        scales: { y: { range: (_u, min, max) => [Math.min(0, min ?? 0), max > 0 ? max * 1.1 : 1] } },
        axes: [
          { ...axis, font: "10px ui-monospace, monospace" },
          {
            ...axis,
            font: "10px ui-monospace, monospace",
            size: 64,
            values: (_u, ticks) => ticks.map((t) => formatRef.current(t)),
          },
        ],
        series: [
          { value: (_u, t) => (t == null ? "" : new Date(t * 1000).toLocaleString()) },
          ...series.map((s, i) => ({
            label: s.label,
            width: 2,
            spanGaps: false,
            points: { show: false },
            value: (_u: uPlot, v: number | null) => (v == null ? "no data" : formatRef.current(v)),
            ...style(i, s.label),
          })),
        ],
      };
      plot = new uPlot(options, aligned(series), el);
    };
    draw();

    const resize = new ResizeObserver(() => plot?.setSize({ width: el.clientWidth, height }));
    resize.observe(el);
    // Light and dark mode swap the tokens: draw again with the new ones.
    const theme = new MutationObserver(draw);
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme"] });
    return () => {
      resize.disconnect();
      theme.disconnect();
      plot?.destroy();
    };
  }, [series, height, dropped, step]);

  if (!series.length) {
    return <p className="px-5 py-8 text-center text-[13px] text-muted-foreground">No data in this range.</p>;
  }

  return (
    <figure className="m-0">
      <div ref={box} className="ordo-uplot w-full text-[12px]" role="img" aria-label={label} />
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
