import { useMemo, useState } from "react";
import { Link, useSearch } from "@tanstack/react-router";
import { ArrowLeft, LayoutGrid, List } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { useHallAllocation } from "@/api/scheduling";
import { api } from "@/lib/api";
import { isSuperAdmin, useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { RelaxedSeatingBadge } from "@/components/relaxed-seating-badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import { EMPTY, formatName, initials, orEmpty } from "@/lib/format";
import { seatNumberGrid } from "@/lib/hall-layout";
import { useQueryClient } from "@tanstack/react-query";

export default function HallAllocationPage() {
  const search = useSearch({ from: "/_app/allocation/hall" });
  const cleanParam = (v: string | undefined) =>
    v && v !== "undefined" && v !== "null" ? v : undefined;
  const date = cleanParam(search.date);
  const period = (cleanParam(search.period) ?? "AM") as "AM" | "PM";
  const hallId = cleanParam(search.hall_id);
  const { user } = useAuth();
  // Only super admins may manually assign seats; committee members view/export only.
  const isAdmin = isSuperAdmin(user);
  const qc = useQueryClient();

  const data = useHallAllocation({
    date,
    period,
    hall_id: hallId ? Number(hallId) : undefined,
  });
  const [seatInputs, setSeatInputs] = useState<Record<number, string>>({});
  const [placedView, setPlacedView] = useState<"list" | "grid">("list");

  // Courses in this hall seated under the relaxed rule (spec 0002).
  const relaxedCodes = useMemo(
    () =>
      new Set(
        (data.data?.courses ?? [])
          .filter((c) => c.seating_rule === "relaxed")
          .map((c) => c.code),
      ),
    [data.data],
  );

  const courseColors = useMemo(() => {
    // Fixed six-color palette. Foreground is chosen for ≥ 4.5:1 contrast
    // against the background; ring is a darker shade of the same hue for
    // borders/accents (and gives white a visible outline).
    const palette: Array<{ bg: string; fg: string; ring: string }> = [
      { bg: "#dc2626", fg: "#ffffff", ring: "#7f1d1d" }, // Red
      { bg: "#2563eb", fg: "#ffffff", ring: "#1e3a8a" }, // Blue
      { bg: "#16a34a", fg: "#ffffff", ring: "#14532d" }, // Green
      { bg: "#facc15", fg: "#111827", ring: "#a16207" }, // Yellow
      { bg: "#9333ea", fg: "#ffffff", ring: "#581c87" }, // Purple
      { bg: "#ffffff", fg: "#111827", ring: "#9ca3af" }, // White
    ];
    const codes = Array.from(
      new Set((data.data?.placed ?? []).map((p) => p.course.code)),
    ).sort();
    const map: Record<string, { bg: string; fg: string; ring: string }> = {};
    codes.forEach((code, i) => {
      map[code] = palette[i % palette.length];
    });
    return map;
  }, [data.data?.placed]);

  const seatBySeatNumber = useMemo(() => {
    const m = new Map<number, NonNullable<typeof data.data>["placed"][number]>();
    (data.data?.placed ?? []).forEach((p) => {
      if (p.seat_number != null) m.set(p.seat_number, p);
    });
    return m;
  }, [data.data?.placed]);

  // Students seated per course, shown in the legend.
  const countByCourse = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const p of data.data?.placed ?? []) {
      counts[p.course.code] = (counts[p.course.code] ?? 0) + 1;
    }
    return counts;
  }, [data.data?.placed]);

  // Seat number per grid cell; null where the hall has no seat.
  const cells = useMemo(() => {
    const hall = data.data?.hall;
    return hall
      ? seatNumberGrid(hall.rows, hall.columns, hall.layout, hall.seat_order)
      : [];
  }, [data.data?.hall]);

  const onAssign = async (saId: number) => {
    const seat = seatInputs[saId];
    if (!seat) return;
    try {
      await api.post("/allocation/manual-assign/", {
        seat_arrangement_id: saId,
        seat_number: Number(seat),
      });
      toast({ title: "Seat assigned" });
      setSeatInputs((s) => ({ ...s, [saId]: "" }));
      qc.invalidateQueries({ queryKey: ["allocation"] });
    } catch (err) {
      toast({
        title: "Assign failed",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <div className="space-y-10">
      <Link
        to="/allocation"
        search={date ? { date, period } : {}}
        className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3" strokeWidth={2.25} />
        Back to allocation
      </Link>

      {!date || !hallId ? (
        <Alert variant="destructive">
          <AlertDescription>
            Missing date or hall in the URL. Open this page from the allocation
            list.
          </AlertDescription>
        </Alert>
      ) : data.isLoading ? (
        <Skeleton className="h-32" />
      ) : data.error ? (
        <Alert variant="destructive">
          <AlertDescription>
            {extractErrorEnvelope(data.error).detail}
          </AlertDescription>
        </Alert>
      ) : data.data ? (
        <>
          <PageHeader
            section="Operations · Allocation"
            title={data.data.hall.name}
            meta={
              <>
                <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                  {data.data.date} · {data.data.period} · {data.data.hall.rows} ×{" "}
                  {data.data.hall.columns} grid · {data.data.hall.seat_count} seats · cap{" "}
                  {data.data.hall.capacity}
                </p>
                {relaxedCodes.size > 0 && (
                  <p className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                    <RelaxedSeatingBadge />
                    <span>
                      <span className="font-mono text-[12px] text-foreground">
                        {[...relaxedCodes].join(", ")}
                      </span>{" "}
                      may sit diagonally next to each other. Watch the diagonals.
                    </span>
                  </p>
                )}
              </>
            }
            actions={
              <div className="grid grid-cols-2 gap-6 self-start sm:self-end">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                    Placed
                  </p>
                  <p className="mt-1 font-serif text-[2rem] tabular-nums text-[color:var(--accent-green-fg)]">
                    {data.data.placed.length}
                  </p>
                </div>
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                    Unplaced
                  </p>
                  <p className="mt-1 font-serif text-[2rem] tabular-nums text-[color:var(--accent-red-fg)]">
                    {data.data.unplaced.length}
                  </p>
                </div>
              </div>
            }
          />

          <Tabs defaultValue="placed">
            <TabsList>
              <TabsTrigger value="placed">
                Placed ({data.data.placed.length})
              </TabsTrigger>
              <TabsTrigger value="unplaced">
                Unplaced ({data.data.unplaced.length})
              </TabsTrigger>
            </TabsList>
            <TabsContent value="placed">
              <div className="mb-4 flex items-center justify-between gap-4">
                <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                  {placedView === "list"
                    ? "Sorted by seat number"
                    : `${data.data.hall.rows} × ${data.data.hall.columns} seat plan · color-coded by course`}
                </p>
                <div className="inline-flex items-center gap-1 border border-border bg-card p-1">
                  <Button
                    type="button"
                    size="xs"
                    variant={placedView === "list" ? "default" : "ghost"}
                    onClick={() => setPlacedView("list")}
                  >
                    <List className="size-3" strokeWidth={2.25} />
                    List
                  </Button>
                  <Button
                    type="button"
                    size="xs"
                    variant={placedView === "grid" ? "default" : "ghost"}
                    onClick={() => setPlacedView("grid")}
                  >
                    <LayoutGrid className="size-3" strokeWidth={2.25} />
                    Grid
                  </Button>
                </div>
              </div>
              {placedView === "grid" ? (
                // overflow-visible: the card's usual overflow-hidden would
                // stop the legend sticking to the page's scroll area.
                <Card className="overflow-visible">
                  <CardContent className="pt-6">
                    {data.data.placed.length === 0 ? (
                      <p className="py-12 text-center font-serif italic text-muted-foreground">
                        Nobody seated yet.
                      </p>
                    ) : (
                      <div className="space-y-8">
                        <div className="sticky top-0 z-20 -mx-(--card-spacing) -mt-6 flex flex-wrap items-center gap-2 border-b border-[color:var(--border)] bg-card/95 px-(--card-spacing) py-3 backdrop-blur-sm">
                          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                            Legend
                          </span>
                          {Object.entries(courseColors).map(([code, palette]) => (
                            <span
                              key={code}
                              className="inline-flex items-center gap-1.5 rounded-[4px] border px-2 py-0.5 font-mono text-[11px] tabular-nums"
                              style={{
                                backgroundColor: palette.bg,
                                color: palette.fg,
                                borderColor: palette.ring,
                              }}
                            >
                              <span
                                className="size-2 rounded-full"
                                style={{ backgroundColor: palette.ring }}
                              />
                              {code}
                              <span className="opacity-80">· {countByCourse[code] ?? 0}</span>
                              {relaxedCodes.has(code) && <RelaxedSeatingBadge />}
                            </span>
                          ))}
                        </div>
                        <div className="flex items-center justify-center gap-3 border-b border-dashed border-[color:var(--border)] pb-4">
                          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                            ←
                          </span>
                          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                            Front of hall · Invigilator
                          </span>
                          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                            →
                          </span>
                        </div>
                        <div className="overflow-x-auto">
                          <div
                            className="mx-auto grid gap-1.5"
                            style={{
                              gridTemplateColumns: `repeat(${data.data.hall.columns}, minmax(56px, 1fr))`,
                              maxWidth: `${data.data.hall.columns * 80}px`,
                            }}
                          >
                            {cells.map((seatNumber, idx) => {
                              if (seatNumber === null) {
                                // No seat here: short row, aisle or pillar.
                                return <div key={`gap-${idx}`} aria-hidden />;
                              }
                              const placed = seatBySeatNumber.get(seatNumber);
                              if (!placed) {
                                return (
                                  <div
                                    key={seatNumber}
                                    className="flex aspect-square flex-col items-center justify-center rounded-[4px] border border-dashed border-[color:var(--border)] bg-[color:var(--muted)]/40 p-1 text-muted-foreground/50"
                                    title={`Seat #${seatNumber} · empty`}
                                  >
                                    <span className="font-mono text-[10px] tabular-nums">
                                      {seatNumber}
                                    </span>
                                  </div>
                                );
                              }
                              const palette = courseColors[placed.course.code];
                              const seatInitials = initials(placed.student?.first_name, placed.student?.last_name);
                              return (
                                <div
                                  key={seatNumber}
                                  className="flex aspect-square flex-col items-center justify-center rounded-[4px] border p-1 transition-shadow hover:shadow-sm"
                                  style={{
                                    backgroundColor: palette?.bg,
                                    color: palette?.fg,
                                    borderColor: palette?.ring,
                                  }}
                                  title={`Seat #${seatNumber} · ${placed.course.code} · ${orEmpty(placed.student?.matric_no)} · ${formatName(placed.student?.first_name, placed.student?.last_name)}`}
                                >
                                  <span className="font-mono text-[10px] font-bold uppercase tracking-[0.1em] opacity-90">
                                    #{seatNumber}
                                  </span>
                                  <span className="mt-0.5 font-serif text-[15px] font-extrabold leading-none tracking-[-0.01em]">
                                    {seatInitials || EMPTY}
                                  </span>
                                  <span className="mt-0.5 font-mono text-[10px] font-bold tabular-nums">
                                    {placed.course.code}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>
              ) : (
              <Card>
                <CardContent className="pt-6">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-[80px]">Seat</TableHead>
                        <TableHead className="w-[180px]">Matric no.</TableHead>
                        <TableHead>Student</TableHead>
                        <TableHead className="w-[120px]">Course</TableHead>
                        <TableHead className="w-[160px]">Class</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.data.placed.length === 0 ? (
                        <TableRow>
                          <TableCell
                            colSpan={5}
                            className="py-12 text-center font-serif italic text-muted-foreground"
                          >
                            Nobody seated yet.
                          </TableCell>
                        </TableRow>
                      ) : (
                        data.data.placed
                          .sort(
                            (a, b) =>
                              (a.seat_number ?? 0) - (b.seat_number ?? 0),
                          )
                          .map((row) => (
                            <TableRow key={row.id}>
                              <TableCell>
                                <kbd className="rounded-[4px] border border-[color:var(--border)] bg-[color:var(--muted)] px-2 py-0.5 font-mono text-[11px] tabular-nums">
                                  #{row.seat_number}
                                </kbd>
                              </TableCell>
                              <TableCell>
                                <span className="font-mono text-[12px] tracking-wide">
                                  {orEmpty(row.student?.matric_no)}
                                </span>
                              </TableCell>
                              <TableCell className="font-serif text-[1rem] tracking-[-0.005em]">
                                {formatName(row.student?.first_name, row.student?.last_name)}
                              </TableCell>
                              <TableCell>
                                <span className="font-mono text-[12px] tracking-wide">
                                  {row.course.code}
                                </span>
                              </TableCell>
                              <TableCell className="text-muted-foreground">
                                {row.class.name}
                              </TableCell>
                            </TableRow>
                          ))
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
              )}
            </TabsContent>
            <TabsContent value="unplaced">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Unplaced students</CardTitle>
                  <CardDescription>
                    {isAdmin
                      ? "Type a seat number and click Assign to place a student manually."
                      : "Only admins can place students manually."}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-[180px]">Matric no.</TableHead>
                        <TableHead>Student</TableHead>
                        <TableHead className="w-[120px]">Course</TableHead>
                        <TableHead className="w-[160px]">Class</TableHead>
                        {isAdmin && (
                          <TableHead className="w-[260px]">Manual seat</TableHead>
                        )}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.data.unplaced.length === 0 ? (
                        <TableRow>
                          <TableCell
                            colSpan={isAdmin ? 5 : 4}
                            className="py-12 text-center font-serif italic text-[color:var(--accent-green-fg)]"
                          >
                            Everyone is seated.
                          </TableCell>
                        </TableRow>
                      ) : (
                        data.data.unplaced.map((row) => (
                          <TableRow key={row.id}>
                            <TableCell>
                              <span className="font-mono text-[12px] tracking-wide">
                                {orEmpty(row.student?.matric_no)}
                              </span>
                            </TableCell>
                            <TableCell className="font-serif text-[1rem] tracking-[-0.005em]">
                              {formatName(row.student?.first_name, row.student?.last_name)}
                            </TableCell>
                            <TableCell>
                              <span className="font-mono text-[12px] tracking-wide">
                                {row.course.code}
                              </span>
                            </TableCell>
                            <TableCell className="text-muted-foreground">
                              {row.class.name}
                            </TableCell>
                            {isAdmin && (
                              <TableCell>
                                <div className="flex gap-2">
                                  <Input
                                    type="number"
                                    min={1}
                                    max={data.data?.hall.seat_count}
                                    placeholder="Seat #"
                                    value={seatInputs[row.id] ?? ""}
                                    onChange={(e) =>
                                      setSeatInputs((s) => ({
                                        ...s,
                                        [row.id]: e.target.value,
                                      }))
                                    }
                                    className="h-8 w-24 font-mono text-[12px]"
                                  />
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={() => onAssign(row.id)}
                                    disabled={!seatInputs[row.id]}
                                  >
                                    Assign
                                  </Button>
                                </div>
                              </TableCell>
                            )}
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </>
      ) : null}
    </div>
  );
}
