import { Fragment, useEffect, useMemo, useState } from "react";
import { Plus } from "lucide-react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  type Hall,
  type HallInput,
  useCreateHall,
  useDeleteHall,
  useHalls,
  useSetHallOpen,
  useUpdateHall,
} from "@/api/halls";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ListShell } from "@/components/data-table/list-shell";
import { PaginationFooter } from "@/components/data-table/pagination";
import { ACTIONS_HEAD_CLASS, RowActions } from "@/components/data-table/row-actions";
import { useAuth } from "@/lib/auth";
import { useConfirm } from "@/lib/confirm";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import {
  ALIGNMENTS,
  SEAT_ORDERS,
  type SeatOrder,
  maskFromRowSeats,
  seatNumberGrid,
} from "@/lib/hall-layout";
import { RowSeatsEditor } from "./row-seats-editor";

/** The letters a hall's name starts with, as the server fills a blank
 * group ("BE 3" → BE). Mirrors ems/halls.py group_from_name. */
function groupFromName(name: string): string {
  const trimmed = name.trim();
  return (trimmed.match(/^[A-Za-z]+/)?.[0] ?? trimmed).toUpperCase();
}

/** How the hall's seats are described in the form. */
type Shape = "rectangle" | "rows";

const schema = z
  .object({
    name: z.string().trim().min(1, "Name is required"),
    group: z.string().trim().max(32),
    capacity: z.coerce.number().int().min(0),
    rows: z.coerce.number().int().min(0),
    columns: z.coerce.number().int().min(0),
    shape: z.enum(["rectangle", "rows"]),
    row_seats: z.array(z.number().int().min(0)),
    align: z.enum(["left", "center", "right"]),
    seat_order: z.enum(["rows", "rows_rtl", "snake", "snake_rtl", "columns", "columns_snake"]),
  })
  .superRefine((v, ctx) => {
    if (v.shape !== "rows") return;
    const message = !v.row_seats.length
      ? "Add at least one row."
      : Math.max(...v.row_seats) <= 0
        ? "The hall needs at least one seat."
        : null;
    if (message) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["row_seats"], message });
    }
  });

type Values = z.infer<typeof schema>;

const EMPTY_VALUES: Values = {
  name: "",
  group: "",
  capacity: 0,
  rows: 0,
  columns: 0,
  shape: "rectangle",
  row_seats: [],
  align: "left",
  seat_order: "rows",
};

/** True if the hall's layout has gaps inside its rows (an aisle, a
 * pillar) that seats per row plus an alignment can not describe. Such a
 * layout comes from the API; the form keeps it unless the rows change. */
function hasCustomLayout(hall: Hall | null): boolean {
  if (!hall?.layout) return false;
  const mask = hall.layout.join("\n");
  return !ALIGNMENTS.some((a) => maskFromRowSeats(hall.row_seats, a).join("\n") === mask);
}

/** True while the form still describes ``hall``'s custom layout untouched. */
function keepsCustomLayout(v: Values, hall: Hall | null): hall is Hall {
  return (
    hasCustomLayout(hall) &&
    v.shape === "rows" &&
    v.align === "left" &&
    v.row_seats.join(",") === hall!.row_seats.join(",")
  );
}

/** Form values for an existing hall. Any layout opens as seats per row. */
function valuesFor(hall: Hall): Values {
  const base: Values = {
    ...EMPTY_VALUES,
    name: hall.name,
    group: hall.group,
    capacity: hall.capacity,
    rows: hall.rows,
    columns: hall.columns,
    row_seats: hall.row_seats,
    seat_order: hall.seat_order,
  };
  if (!hall.layout) return base;
  const mask = hall.layout.join("\n");
  const align =
    ALIGNMENTS.find((a) => maskFromRowSeats(hall.row_seats, a).join("\n") === mask) ??
    "left";
  return { ...base, shape: "rows", align };
}

/** The request body for the chosen shape. Values are already validated. */
function toInput(v: Values, initial: Hall | null): HallInput {
  const base = {
    name: v.name,
    group: v.group.toUpperCase(),
    capacity: v.capacity,
    seat_order: v.seat_order,
  };
  if (keepsCustomLayout(v, initial)) {
    return { ...base, rows: initial.rows, columns: initial.columns, layout: initial.layout };
  }
  if (v.shape === "rows") {
    return {
      ...base,
      rows: v.row_seats.length,
      columns: Math.max(...v.row_seats),
      row_seats: v.row_seats,
      align: v.align,
    };
  }
  return { ...base, rows: v.rows, columns: v.columns, layout: null };
}

/** rows, columns and mask the form currently describes, or null while the
 * input is incomplete. */
function previewOf(
  v: Values,
  initial: Hall | null,
): { rows: number; columns: number; layout: string[] | null } | null {
  if (keepsCustomLayout(v, initial)) {
    return { rows: initial.rows, columns: initial.columns, layout: initial.layout };
  }
  if (v.shape === "rows") {
    const columns = Math.max(0, ...v.row_seats);
    return columns > 0
      ? { rows: v.row_seats.length, columns, layout: maskFromRowSeats(v.row_seats, v.align) }
      : null;
  }
  const rows = Number(v.rows);
  const columns = Number(v.columns);
  return rows > 0 && columns > 0 ? { rows, columns, layout: null } : null;
}

export default function HallsListPage() {
  const { user } = useAuth();
  const isAdmin = !!user?.is_staff;

  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<Hall | null>(null);
  const [open, setOpen] = useState(false);

  const list = useHalls({ page, query: query || undefined });
  const remove = useDeleteHall();
  const setHallOpen = useSetHallOpen();
  const confirm = useConfirm();

  const onToggleOpen = async (h: Hall, is_open: boolean) => {
    try {
      await setHallOpen.mutateAsync({ id: h.id, is_open });
      toast({
        title: is_open ? "Hall opened" : "Hall closed",
        description: is_open
          ? `${h.name} is used in every generate run again.`
          : `${h.name} is left out of the timetable, distribution and allocation.`,
      });
    } catch (err) {
      toast({
        title: "Could not change the hall",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  const onDelete = async (h: Hall) => {
    const ok = await confirm({
      title: "Delete hall?",
      description: `Hall ${h.name} will be permanently removed.`,
      confirmLabel: "Delete",
      destructive: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(h.id);
      toast({ title: "Hall deleted" });
    } catch (err) {
      toast({
        title: "Delete failed",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <>
      <ListShell
        section="Catalog · Halls"
        title="Halls"
        emptyMessage={query ? "No halls match your search." : "No halls yet."}
        description="The venues you run exams in, and how their seats are laid out."
        toolbar={
          isAdmin && (
            <Button onClick={() => { setEditing(null); setOpen(true); }}>
              <Plus data-icon="inline-start" /> New hall
            </Button>
          )
        }
        query={query}
        onQueryChange={(q) => { setQuery(q); setPage(1); }}
        searchPlaceholder="Search halls"
        isLoading={list.isLoading}
        error={list.error}
        isEmpty={!list.data?.results.length}
        pagination={
          list.data && (
            <PaginationFooter
              page={page}
              pageSize={15}
              total={list.data.count}
              onPageChange={setPage}
            />
          )
        }
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Group</TableHead>
              <TableHead className="text-right">Capacity</TableHead>
              <TableHead className="text-right">Rows × Cols</TableHead>
              <TableHead className="text-right">Seats</TableHead>
              <TableHead className="w-[90px]">Open</TableHead>
              {isAdmin && <TableHead className={ACTIONS_HEAD_CLASS}>Actions</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.results.map((h) => (
              <TableRow
                key={h.id}
                className={h.is_open ? undefined : "text-muted-foreground"}
              >
                <TableCell className="font-medium">{h.name}</TableCell>
                <TableCell className="font-mono text-xs">{h.group}</TableCell>
                <TableCell className="text-right">{h.capacity}</TableCell>
                <TableCell className="text-right">
                  {h.rows} × {h.columns}
                </TableCell>
                <TableCell className="text-right">
                  {h.seat_count}
                  {h.layout && (
                    <span className="ml-1.5 text-xs text-muted-foreground">shaped</span>
                  )}
                </TableCell>
                <TableCell>
                  {isAdmin ? (
                    <Switch
                      checked={h.is_open}
                      onCheckedChange={(v) => onToggleOpen(h, v)}
                      disabled={setHallOpen.isPending}
                      aria-label={`${h.name} is open for exams`}
                    />
                  ) : (
                    <span className="font-mono text-[11px]">
                      {h.is_open ? "Yes" : "Closed"}
                    </span>
                  )}
                </TableCell>
                {isAdmin && (
                  <TableCell className="text-right">
                    <RowActions>
                      <Button
                        size="xs"
                        variant="outline"
                        onClick={() => { setEditing(h); setOpen(true); }}
                      >
                        Edit
                      </Button>
                      <Button
                        size="xs"
                        variant="destructive"
                        onClick={() => onDelete(h)}
                        disabled={remove.isPending}
                      >
                        Delete
                      </Button>
                    </RowActions>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </ListShell>
      <HallFormDialog open={open} onOpenChange={setOpen} initial={editing} />
    </>
  );
}

function HallFormDialog({
  open,
  onOpenChange,
  initial,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  initial: Hall | null;
}) {
  const isEdit = !!initial;
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: EMPTY_VALUES,
  });
  const create = useCreateHall();
  const update = useUpdateHall(initial?.id ?? 0);

  useEffect(() => {
    if (!open) return;
    form.reset(initial ? valuesFor(initial) : EMPTY_VALUES);
    setTopError(null);
  }, [open, initial, form]);

  const watched = form.watch();
  const shape = watched.shape;
  const preview = previewOf(watched, initial);
  const customLayout = hasCustomLayout(initial);

  const onSubmit = async (v: Values) => {
    setTopError(null);
    try {
      if (isEdit) {
        await update.mutateAsync(toInput(v, initial));
        toast({ title: "Hall updated" });
      } else {
        await create.mutateAsync(toInput(v, initial));
        toast({ title: "Hall created" });
      }
      onOpenChange(false);
    } catch (err) {
      const env = extractErrorEnvelope(err);
      setTopError(env.detail);
      if (env.errors) {
        for (const [k, msgs] of Object.entries(env.errors)) {
          // The server reports every shape problem under "layout"; show it
          // on the input the chosen shape uses.
          const field =
            k === "layout" || k === "row_seats"
              ? v.shape === "rows" ? "row_seats" : "rows"
              : k;
          if (["name", "group", "capacity", "rows", "columns", "row_seats"].includes(field)) {
            form.setError(field as keyof Values, { message: msgs.join(", ") });
          }
        }
      }
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[92vh] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-[min(1400px,calc(100%-2rem))]">
        <DialogHeader className="border-b border-border px-6 py-5 pr-16">
          <DialogTitle>{isEdit ? "Edit hall" : "New hall"}</DialogTitle>
          <DialogDescription>
            Capacity is the most students the hall is rated for. The shape is
            where the seats physically are, and seat allocation follows it.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex min-h-0 flex-1 flex-col"
          >
            <div className="min-h-0 flex-1 overflow-y-auto lg:grid lg:grid-cols-[420px_minmax(0,1fr)] lg:overflow-hidden">
              <div className="space-y-5 border-b border-border p-6 lg:overflow-y-auto lg:border-r lg:border-b-0">
                {topError && (
                  <Alert variant="destructive">
                    <AlertDescription>{topError}</AlertDescription>
                  </Alert>
                )}
                <FormField
                  control={form.control}
                  name="name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Name</FormLabel>
                      <FormControl><Input {...field} /></FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="group"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Group</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          placeholder={groupFromName(watched.name) || "e.g. BE"}
                          className="uppercase"
                        />
                      </FormControl>
                      <p className="text-xs text-muted-foreground">
                        Halls that stand together. Leave blank to use the
                        letters the name starts with.
                      </p>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="capacity"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Capacity</FormLabel>
                      <FormControl>
                        <Input type="number" min={0} {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <div className="space-y-3">
                  <FormLabel>Seat numbering</FormLabel>
                  <SeatOrderPicker
                    value={watched.seat_order}
                    onChange={(order) => form.setValue("seat_order", order, { shouldDirty: true })}
                  />
                  {isEdit && initial && watched.seat_order !== initial.seat_order && (
                    <p className="text-xs text-[color:var(--accent-red-fg)]">
                      This renumbers the hall. Run allocation again for any slot
                      already seated here.
                    </p>
                  )}
                </div>
                <div className="space-y-3">
                  <FormLabel>Shape</FormLabel>
                  <Tabs
                    value={shape}
                    onValueChange={(value) => {
                      form.setValue("shape", value as Shape, { shouldValidate: false });
                      // Start seats per row from the rectangle already typed.
                      const { rows, columns, row_seats } = form.getValues();
                      if (value === "rows" && !row_seats.length && rows > 0 && columns > 0) {
                        form.setValue("row_seats", Array(Number(rows)).fill(Number(columns)));
                      }
                    }}
                  >
                    <TabsList className="w-full">
                      <TabsTrigger value="rectangle">Rectangle</TabsTrigger>
                      <TabsTrigger value="rows">Seats per row</TabsTrigger>
                    </TabsList>
                  </Tabs>
                  {shape === "rectangle" && (
                    <div className="grid grid-cols-2 gap-3">
                      {(
                        [
                          ["rows", "Rows"],
                          ["columns", "Columns"],
                        ] as const
                      ).map(([name, label]) => (
                        <FormField
                          key={name}
                          control={form.control}
                          name={name}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>{label}</FormLabel>
                              <FormControl>
                                <Input type="number" min={0} {...field} />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      ))}
                    </div>
                  )}
                  {shape === "rows" && (
                    <>
                      {customLayout && (
                        <Alert>
                          <AlertDescription>
                            This hall has gaps inside its rows, such as an
                            aisle. They are kept unless you change the rows or
                            where short rows sit.
                          </AlertDescription>
                        </Alert>
                      )}
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs text-muted-foreground">Short rows sit</span>
                        {ALIGNMENTS.map((a) => (
                          <Button
                            key={a}
                            type="button"
                            size="xs"
                            variant={watched.align === a ? "default" : "outline"}
                            onClick={() => form.setValue("align", a)}
                          >
                            {a === "center" ? "centred" : `on the ${a}`}
                          </Button>
                        ))}
                      </div>
                      <FormField
                        control={form.control}
                        name="row_seats"
                        render={({ field }) => (
                          <FormItem>
                            <RowSeatsEditor
                              value={field.value}
                              onChange={(next) =>
                                form.setValue("row_seats", next, {
                                  shouldDirty: true,
                                  shouldValidate: form.formState.isSubmitted,
                                })
                              }
                            />
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </>
                  )}
                </div>
              </div>
              <div className="min-h-[360px] bg-muted/30 p-6 lg:min-h-0 lg:overflow-auto">
                <HallPreview preview={preview} seatOrder={watched.seat_order} />
              </div>
            </div>
            <DialogFooter className="border-t border-border px-6 py-4">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting
                  ? "Saving…"
                  : isEdit
                    ? "Save changes"
                    : "Create"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

/** Past this many cells a drawing is too dense to be useful; show counts only. */
const PREVIEW_MAX_CELLS = 5000;
/** Up to this many columns each seat is big enough to show its number. */
const NUMBERED_MAX_COLUMNS = 30;

/** The hall as allocation will see it: every seat with its number, front
 * of the hall on top, each row labelled with its seat count. */
function HallPreview({
  preview,
  seatOrder,
}: {
  preview: { rows: number; columns: number; layout: string[] | null } | null;
  seatOrder: SeatOrder;
}) {
  const rows = preview?.rows ?? 0;
  const columns = preview?.columns ?? 0;
  const layoutKey = preview?.layout?.join("\n") ?? "";
  const cells = useMemo(
    () => (preview ? seatNumberGrid(rows, columns, preview.layout, seatOrder) : []),
    // layoutKey stands in for the layout array, which is rebuilt every render.
    [rows, columns, layoutKey, seatOrder],
  );

  if (!preview) {
    return (
      <div className="flex h-full min-h-[300px] items-center justify-center border border-dashed border-border">
        <p className="font-serif italic text-muted-foreground">
          Describe the hall's shape to see its seat plan.
        </p>
      </div>
    );
  }

  const seats = cells.filter((c) => c !== null).length;
  const perRow = Array.from({ length: rows }, (_, r) =>
    cells.slice(r * columns, (r + 1) * columns).filter((c) => c !== null).length,
  );
  const numbered = columns <= NUMBERED_MAX_COLUMNS;

  return (
    <div className="flex h-full flex-col gap-5">
      <div className="flex flex-wrap gap-x-10 gap-y-3">
        {(
          [
            ["Seats", seats],
            ["Grid", `${rows} × ${columns}`],
            ["No seat", rows * columns - seats],
          ] as const
        ).map(([label, value]) => (
          <div key={label}>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
              {label}
            </p>
            <p className="mt-1 font-serif text-[1.75rem] leading-none tabular-nums">
              {value}
            </p>
          </div>
        ))}
      </div>
      {cells.length > PREVIEW_MAX_CELLS ? (
        <p className="text-muted-foreground">
          Too many cells to draw ({cells.length}). The counts above are exact.
        </p>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <div
            className="mx-auto"
            style={{ maxWidth: `${columns * 44 + 64}px` }}
          >
            <p className="mb-3 border-b border-dashed border-border pb-2 text-center font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              Front of hall · Invigilator
            </p>
            <div
              className="grid items-center gap-1"
              style={{
                gridTemplateColumns: `3.5rem repeat(${columns}, minmax(10px, 1fr))`,
              }}
            >
              {perRow.map((count, r) => (
                <Fragment key={r}>
                  <span
                    className="pr-2 text-right font-mono text-[10px] tabular-nums text-muted-foreground"
                    title={`Row ${r + 1}: ${count} seats`}
                  >
                    R{r + 1} · {count}
                  </span>
                  {cells.slice(r * columns, (r + 1) * columns).map((seat, c) =>
                    seat === null ? (
                      <span key={c} className="aspect-square" aria-hidden />
                    ) : (
                      <span
                        key={c}
                        className="flex aspect-square items-center justify-center rounded-[3px] border border-border bg-card font-mono text-[10px] tabular-nums text-muted-foreground"
                        title={`Seat ${seat}`}
                      >
                        {numbered ? seat : null}
                      </span>
                    ),
                  )}
                </Fragment>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** The six numbering orders, each drawn on a small 3 x 4 hall. */
function SeatOrderPicker({
  value,
  onChange,
}: {
  value: SeatOrder;
  onChange: (order: SeatOrder) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {SEAT_ORDERS.map(({ value: order, label }) => {
        const active = value === order;
        return (
          <button
            key={order}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(order)}
            className={
              "flex flex-col items-center gap-2 border px-2 py-2.5 text-center transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/30 " +
              (active ? "border-primary bg-muted" : "border-border hover:bg-muted")
            }
          >
            <span
              className="grid gap-0.5"
              style={{ gridTemplateColumns: "repeat(4, 1.125rem)" }}
              aria-hidden
            >
              {seatNumberGrid(3, 4, null, order).map((n, i) => (
                <span
                  key={i}
                  className={
                    "flex h-4 items-center justify-center rounded-[2px] font-mono text-[9px] tabular-nums " +
                    (n === 1 ? "bg-primary text-primary-foreground" : "bg-foreground/10")
                  }
                >
                  {n}
                </span>
              ))}
            </span>
            <span className="text-[11px] leading-tight">{label}</span>
          </button>
        );
      })}
    </div>
  );
}
