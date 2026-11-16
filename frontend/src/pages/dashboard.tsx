import { Link } from "@tanstack/react-router";
import type { AppPath } from "@/lib/paths";
import { type FeatureKey, useSchoolPlan } from "@/api/school";
import { useDashboardStats } from "@/api/system";
import { InstitutionName } from "@/components/institution-logo";
import { useAuth } from "@/lib/auth";
import { useReveal } from "@/lib/use-reveal";
import {
  ArrowUpRight,
  Building2,
  CalendarDays,
  ClipboardList,
  GraduationCap,
  Grid3x3,
  School,
  Sparkles,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { formatEmail, formatName, isBlank } from "@/lib/format";

const TILES: Array<{
  key:
    | "departments_count"
    | "classes_count"
    | "courses_count"
    | "halls_count"
    | "students_count";
  label: string;
  icon: typeof Building2;
  to: AppPath;
  /** Admin-only routes/data — staff users should not see these tiles. */
  adminOnly?: boolean;
}> = [
  {
    key: "departments_count",
    label: "Departments",
    icon: Building2,
    to: "/departments",
    adminOnly: true,
  },
  {
    key: "classes_count",
    label: "Classes",
    icon: School,
    to: "/classes",
  },
  {
    key: "courses_count",
    label: "Courses",
    icon: ClipboardList,
    to: "/courses",
  },
  {
    key: "halls_count",
    label: "Halls",
    icon: Grid3x3,
    to: "/halls",
    adminOnly: true,
  },
  {
    key: "students_count",
    label: "Students",
    icon: GraduationCap,
    to: "/students",
  },
];

const OPS: Array<{ to: AppPath; label: string; hint: string; feature?: FeatureKey }> = [
  {
    to: "/timetable",
    label: "Timetable",
    hint: "Generate or review the session grid.",
  },
  {
    to: "/distribution",
    label: "Distribution",
    hint: "Capacity-aware hall assignment.",
    feature: "distribution",
  },
  {
    to: "/allocation",
    label: "Allocation",
    hint: "Seat-by-seat anti-cheating placement.",
    feature: "seat_allocation",
  },
  {
    to: "/jobs",
    label: "Jobs",
    hint: "Live progress for in-flight runs.",
  },
];

export default function DashboardPage() {
  useReveal();
  const { user } = useAuth();
  const stats = useDashboardStats();
  const greeting = (() => {
    const h = new Date().getHours();
    if (h < 12) return "Good morning";
    if (h < 17) return "Good afternoon";
    return "Good evening";
  })();
  const today = new Date().toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  const visibleTiles = TILES.filter((t) => !t.adminOnly || user?.is_staff);
  const plan = useSchoolPlan();
  const ops = OPS.filter((op) => !op.feature || !plan.data || plan.data.features.includes(op.feature));
  // Static class strings so Tailwind can statically analyse them.
  const tileColsClass =
    visibleTiles.length >= 5
      ? "lg:grid-cols-5"
      : visibleTiles.length === 4
        ? "lg:grid-cols-4"
        : "lg:grid-cols-3";

  return (
    <div className="space-y-14">
      {/* HEADER — solid brand-soft pane */}
      <header
        data-reveal
        className="relative overflow-hidden rounded-[20px] border border-[color:var(--border)]"
        style={{ backgroundColor: "var(--brand-soft)" }}
      >
        <div className="relative flex flex-col gap-8 px-8 py-10 sm:flex-row sm:items-end sm:justify-between sm:px-12 sm:py-14">
          <div>
            <InstitutionName className="mb-4 block font-serif text-[1.5rem] leading-tight tracking-tight text-[color:var(--brand-strong)]" />
            <div className="flex flex-wrap items-center gap-2">
              <span
                className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.16em]"
                style={{
                  backgroundColor: "var(--brand)",
                  color: "var(--brand-foreground)",
                }}
              >
                <Sparkles className="size-3" strokeWidth={2.25} />
                Overview
              </span>
              <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                {today}
              </span>
            </div>
            <h1 className="mt-5 max-w-[18ch] font-serif text-[2.75rem] leading-[1.02] tracking-[-0.02em] text-balance sm:text-[3.5rem]">
              {greeting},{" "}
              <span
                className="italic"
                style={{ color: "var(--brand-strong)" }}
              >
                {isBlank(user?.first_name)
                  ? formatEmail(user?.email)
                  : formatName(user?.first_name)}
              </span>
              .
            </h1>
          </div>
          <dl className="flex flex-col items-end gap-1 self-start sm:self-end">
            <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
              Active session
            </dt>
            {stats.isLoading ? (
              <Skeleton className="h-7 w-32" />
            ) : (
              <dd className="font-serif text-[1.625rem] leading-tight tabular-nums">
                {stats.data?.settings.session}
                <span
                  className="ml-2 inline-block rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em]"
                  style={{
                    backgroundColor: "var(--card)",
                    color: "var(--brand-strong)",
                  }}
                >
                  {stats.data?.settings.semester}
                </span>
              </dd>
            )}
          </dl>
        </div>
      </header>

      {/* STATS — brand-coded tiles */}
      <section>
        <div data-reveal className="mb-6 flex items-end justify-between">
          <div className="flex items-center gap-2">
            <span
              aria-hidden
              className="size-1.5 rounded-full animate-pulse-soft"
              style={{ backgroundColor: "var(--brand)" }}
            />
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
              01 · Statistics
            </p>
          </div>
          <Link
            to="/departments"
            className="group inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground transition-colors hover:text-[color:var(--brand-strong)]"
          >
            View all
            <ArrowUpRight
              className="size-3 transition-transform group-hover:-translate-y-px group-hover:translate-x-px"
              strokeWidth={2.25}
            />
          </Link>
        </div>
        <div className={`grid grid-cols-2 gap-3 sm:grid-cols-3 ${tileColsClass}`}>
          {visibleTiles.map(({ key, label, icon: Icon, to }, i) => (
            <Link
              key={key}
              to={to}
              data-reveal
              style={
                {
                  "--reveal-delay": `${i * 70}ms`,
                } as React.CSSProperties
              }
              className="group relative flex flex-col justify-between gap-6 overflow-hidden rounded-[14px] border border-[color:var(--border)] bg-card p-5 transition-all duration-300 hover:-translate-y-0.5 hover:border-[color:var(--brand)]/40 hover:shadow-[0_18px_40px_-22px_color-mix(in_oklch,var(--brand)_55%,transparent)]"
            >
              {/* Solid brand rail at top, scales in on hover */}
              <span
                aria-hidden
                className="absolute inset-x-0 top-0 h-[3px] origin-left scale-x-0 transition-transform duration-500 ease-out group-hover:scale-x-100"
                style={{ backgroundColor: "var(--brand)" }}
              />
              <div className="flex items-start justify-between">
                <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                  {label}
                </span>
                <span
                  className="grid size-7 place-items-center rounded-md transition-colors"
                  style={{
                    backgroundColor: "var(--brand-soft)",
                    color: "var(--brand-strong)",
                  }}
                >
                  <Icon className="size-3.5" strokeWidth={2} />
                </span>
              </div>
              {stats.isLoading ? (
                <Skeleton className="h-10 w-24" />
              ) : (
                <div className="font-serif text-[2.5rem] leading-none tabular-nums tracking-[-0.02em]">
                  {(stats.data?.[key] ?? 0).toLocaleString()}
                </div>
              )}
            </Link>
          ))}
        </div>
      </section>

      {/* OPERATIONS — brand chips, hint as the lead */}
      <section>
        <div data-reveal className="mb-6 flex items-end justify-between">
          <div className="flex items-center gap-2">
            <span
              aria-hidden
              className="size-1.5 rounded-full animate-pulse-soft"
              style={{ backgroundColor: "var(--brand)" }}
            />
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
              02 · Quick Actions
            </p>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {ops.map((op, i) => (
            <Link
              key={op.to}
              to={op.to}
              data-reveal
              style={
                {
                  "--reveal-delay": `${i * 90}ms`,
                } as React.CSSProperties
              }
              className="group relative flex flex-col justify-between gap-7 overflow-hidden rounded-[14px] border border-[color:var(--border)] bg-card p-5 transition-all duration-300 hover:-translate-y-0.5 hover:border-[color:var(--brand)]/40 hover:shadow-[0_22px_44px_-26px_color-mix(in_oklch,var(--brand)_60%,transparent)]"
            >
              <div className="relative flex items-center justify-between">
                <span
                  className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-mono text-[9px] uppercase tracking-[0.16em]"
                  style={{
                    backgroundColor: "var(--brand-soft)",
                    color: "var(--brand-strong)",
                  }}
                >
                  <span
                    aria-hidden
                    className="size-1 rounded-full"
                    style={{ backgroundColor: "var(--brand-strong)" }}
                  />
                  {op.label}
                </span>
                <ArrowUpRight
                  className="size-3.5 text-muted-foreground transition-all duration-300 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-[color:var(--brand-strong)]"
                  strokeWidth={2.25}
                />
              </div>
              <p className="relative font-serif text-[1.25rem] leading-snug tracking-[-0.005em]">
                {op.hint}
              </p>
            </Link>
          ))}
        </div>
      </section>

      {/* SHARED COURSES (admin) */}
      {user?.is_staff && (stats.data?.shared_courses_count ?? 0) > 0 && (
        <section>
          <div data-reveal className="mb-6 flex items-end justify-between">
            <div>
              <div className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="size-1.5 rounded-full animate-pulse-soft"
                  style={{ backgroundColor: "var(--brand)" }}
                />
                <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                  03 · Cross-department
                </p>
              </div>
              <h2 className="mt-2 flex flex-wrap items-center gap-3 font-serif text-[2rem] leading-[1.05] tracking-[-0.015em]">
                Shared courses
                <span
                  className="rounded-full px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em]"
                  style={{
                    backgroundColor: "var(--brand-soft)",
                    color: "var(--brand-strong)",
                  }}
                >
                  {stats.data?.shared_courses_count} flagged
                </span>
              </h2>
            </div>
            <CalendarDays
              className="size-4 text-muted-foreground"
              strokeWidth={2}
            />
          </div>
          <p className="mb-6 max-w-2xl text-[14px] leading-[1.65] text-muted-foreground">
            Courses taken by more than one department. Each bar shows how the course's classes are spread out. Hover over a segment to see the department and number of classes.
          </p>

          {(() => {
            const courses = stats.data?.shared_courses ?? [];
            const courseTotals = courses.map((c) =>
              c.departments.reduce(
                (n, d) => n + d.classes.filter(Boolean).length,
                0,
              ),
            );
            const maxClasses = Math.max(1, ...courseTotals);

            return (
              <div
                data-reveal
                className="rounded-[16px] border border-[color:var(--border)] bg-card p-6 sm:p-7"
              >
                {/* Axis label */}
                <div className="mb-5 flex items-baseline justify-between font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                  <span>Course · departments</span>
                  <span className="tabular-nums">
                    classes 0 to {maxClasses}
                  </span>
                </div>

                <ol className="space-y-3">
                  {courses.map((c, idx) => {
                    const total = courseTotals[idx];
                    const widthPct = (total / maxClasses) * 100;
                    const segments = c.departments
                      .map((d) => ({
                        name: d.name,
                        cls: d.classes.filter(Boolean).length,
                      }))
                      .filter((s) => s.cls > 0);
                    return (
                      <li
                        key={c.code}
                        className="group/row grid items-center gap-x-3 gap-y-1"
                        style={{
                          gridTemplateColumns:
                            "minmax(72px, 88px) minmax(0, 1fr) 28px",
                        }}
                      >
                        {/* Code */}
                        <span
                          title={c.name}
                          className="truncate font-mono text-[11px] uppercase tracking-[0.14em] tabular-nums"
                          style={{ color: "var(--brand-strong)" }}
                        >
                          {c.code}
                        </span>

                        {/* Stacked distribution bar */}
                        <div className="relative h-7 overflow-hidden rounded-[6px] bg-muted">
                          <div
                            data-fill
                            className="flex h-full"
                            style={
                              {
                                "--fill-w": `${widthPct}%`,
                                "--reveal-delay": `${idx * 70}ms`,
                              } as React.CSSProperties
                            }
                          >
                            {segments.map((s) => {
                              const segPct = (s.cls / total) * 100;
                              const label = `${s.cls === 1 ? "1 class" : `${s.cls} classes`}`;
                              return (
                                <span
                                  key={s.name}
                                  title={`${s.name} · ${label}`}
                                  className="h-full transition-opacity duration-200 hover:opacity-90"
                                  style={{
                                    width: `${segPct}%`,
                                    backgroundColor: "var(--brand)",
                                  }}
                                />
                              );
                            })}
                          </div>
                        </div>

                        {/* Total */}
                        <span className="text-right font-mono text-[10px] tabular-nums text-muted-foreground">
                          {total}
                        </span>

                        {/* Course name on a tight second line */}
                        <span
                          className="col-start-2 truncate font-serif text-[12.5px] italic leading-snug text-muted-foreground"
                          title={c.name}
                        >
                          {c.name}
                        </span>
                      </li>
                    );
                  })}
                </ol>

              </div>
            );
          })()}
        </section>
      )}
    </div>
  );
}
