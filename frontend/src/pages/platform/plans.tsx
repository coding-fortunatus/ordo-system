import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Archive, ArchiveRestore, Pencil, Plus, Trash2 } from "lucide-react";
import {
  FEATURE_LABELS,
  missingFeatureRequirement,
  SEAT_ALLOCATION_NEEDS_DISTRIBUTION,
  type FeatureKey,
  type PlanInput,
  type PlanRow,
  useCreatePlan,
  useDeletePlan,
  usePlans,
  useUpdatePlan,
} from "@/api/platform";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ListShell } from "@/components/data-table/list-shell";
import { ACTIONS_HEAD_CLASS, RowActions } from "@/components/data-table/row-actions";
import { extractErrorEnvelope } from "@/lib/api";
import { isPlatformOwner, usePlatformAuth } from "@/lib/platform-auth";
import { toast } from "@/lib/use-toast";
import { FeatureChecklist } from "./school-form";

/** "By quotation", or the price in naira with separators. */
export function priceLabel(value: number | null): string {
  return value === null ? "By quotation" : `₦${value.toLocaleString()}`;
}

/** "Unlimited", or the number with separators. */
export function limitLabel(value: number | null): string {
  return value === null ? "Unlimited" : value.toLocaleString();
}

export default function ConsolePlansPage() {
  const list = usePlans();
  const { admin } = usePlatformAuth();
  const owner = isPlatformOwner(admin);
  // undefined: closed, null: a new plan, a row: editing it.
  const [editing, setEditing] = useState<PlanRow | null | undefined>(undefined);
  const [deleting, setDeleting] = useState<PlanRow | null>(null);
  const update = useUpdatePlan();

  const setOffered = async (plan: PlanRow, is_active: boolean) => {
    try {
      await update.mutateAsync({ id: plan.id, is_active });
      toast({
        title: is_active ? `${plan.name} restored` : `${plan.name} archived`,
        description: is_active
          ? "New schools can pick it again."
          : "New schools can't pick it. Schools already on it keep it.",
      });
    } catch (err) {
      toast({
        title: "Couldn't change the plan",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <>
      <ListShell
        section="Console · Manage"
        title="Plans"
        description="What a school can use: how many students and staff accounts, and which features, and what it costs per academic session. Any school can override a limit. Archive a plan to stop offering it; only a plan no school has ever been on can be deleted."
        toolbar={
          owner && (
            <Button variant="brand" onClick={() => setEditing(null)}>
              <Plus data-icon="inline-start" />
              New plan
            </Button>
          )
        }
        isLoading={list.isLoading}
        error={list.error}
        isEmpty={!list.data?.length}
        emptyMessage="No plans yet."
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Plan</TableHead>
              <TableHead className="text-right">Students</TableHead>
              <TableHead className="text-right">Staff</TableHead>
              <TableHead className="text-right">Price / session</TableHead>
              <TableHead>Features</TableHead>
              <TableHead className="text-right">Schools</TableHead>
              <TableHead>Status</TableHead>
              {owner && (
                <TableHead className={ACTIONS_HEAD_CLASS}>
                  <span className="sr-only">Actions</span>
                </TableHead>
              )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.map((plan) => (
              <TableRow key={plan.id}>
                <TableCell>
                  <span className="block font-medium">{plan.name}</span>
                  <span className="block font-mono text-[12px] text-muted-foreground">{plan.code}</span>
                </TableCell>
                <TableCell className="text-right tabular-nums">{limitLabel(plan.max_students)}</TableCell>
                <TableCell className="text-right tabular-nums">{limitLabel(plan.max_staff_users)}</TableCell>
                <TableCell className="text-right tabular-nums">{priceLabel(plan.price)}</TableCell>
                <TableCell className="min-w-[240px] max-w-[360px] whitespace-normal text-[13px] leading-snug">
                  {plan.features.length ? (
                    plan.features.map((f) => FEATURE_LABELS[f]).join(", ")
                  ) : (
                    <span className="text-muted-foreground">None</span>
                  )}
                </TableCell>
                <TableCell className="text-right tabular-nums">{plan.school_count}</TableCell>
                <TableCell>
                  <Badge variant={plan.is_active ? "secondary" : "outline"}>
                    {plan.is_active ? "Offered" : "Archived"}
                  </Badge>
                </TableCell>
                {owner && (
                  <TableCell>
                    <RowActions>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditing(plan)}
                        aria-label={`Edit ${plan.name}`}
                      >
                        <Pencil data-icon="inline-start" />
                        Edit
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={update.isPending}
                        onClick={() => setOffered(plan, !plan.is_active)}
                        aria-label={`${plan.is_active ? "Archive" : "Restore"} ${plan.name}`}
                      >
                        {plan.is_active ? (
                          <Archive data-icon="inline-start" />
                        ) : (
                          <ArchiveRestore data-icon="inline-start" />
                        )}
                        {plan.is_active ? "Archive" : "Restore"}
                      </Button>
                      {plan.deletable && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive"
                          onClick={() => setDeleting(plan)}
                          aria-label={`Delete ${plan.name}`}
                        >
                          <Trash2 data-icon="inline-start" />
                          Delete
                        </Button>
                      )}
                    </RowActions>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </ListShell>
      <PlanDialog plan={editing} onClose={() => setEditing(undefined)} />
      <DeletePlanDialog plan={deleting} onClose={() => setDeleting(null)} />
    </>
  );
}

const optionalCount = z
  .string()
  .trim()
  .regex(/^\d*$/, "Use a whole number, or leave it blank for unlimited");

const planSchema = z.object({
  code: z
    .string()
    .trim()
    .regex(/^[a-z0-9-_]+$/, "Lowercase letters, digits, hyphens or underscores"),
  name: z.string().trim().min(1, "Name is required").max(100),
  max_students: optionalCount,
  max_staff_users: optionalCount,
  price: z
    .string()
    .trim()
    .regex(/^\d*$/, "Use whole naira, or leave it blank for priced by quotation"),
  features: z.array(z.enum(["distribution", "seat_allocation", "exports", "publication", "branding", "portal_sync"])),
  is_active: z.boolean(),
}).refine((v) => !missingFeatureRequirement(v.features), {
  path: ["features"],
  message: SEAT_ALLOCATION_NEEDS_DISTRIBUTION,
});

type PlanValues = z.infer<typeof planSchema>;

const PLAN_FIELDS = ["code", "name", "max_students", "max_staff_users", "price", "features", "is_active"] as const;

const count = (value: string) => (value === "" ? null : Number(value));

function toValues(plan: PlanRow | null): PlanValues {
  return {
    code: plan?.code ?? "",
    name: plan?.name ?? "",
    max_students: plan?.max_students?.toString() ?? "",
    max_staff_users: plan?.max_staff_users?.toString() ?? "",
    price: plan?.price?.toString() ?? "",
    features: plan?.features ?? [],
    is_active: plan?.is_active ?? true,
  };
}

function toInput(v: PlanValues): PlanInput {
  return {
    code: v.code,
    name: v.name,
    max_students: count(v.max_students),
    max_staff_users: count(v.max_staff_users),
    price: count(v.price),
    features: v.features as FeatureKey[],
    is_active: v.is_active,
  };
}

function PlanDialog({ plan, onClose }: { plan: PlanRow | null | undefined; onClose: () => void }) {
  const create = useCreatePlan();
  const update = useUpdatePlan();
  const [error, setError] = useState<string | null>(null);
  const form = useForm<PlanValues>({
    resolver: zodResolver(planSchema),
    defaultValues: toValues(null),
  });
  const open = plan !== undefined;
  const editing = plan ?? null;

  useEffect(() => {
    if (open) {
      form.reset(toValues(editing));
      setError(null);
    }
  }, [open, editing, form]);

  const onSubmit = async (values: PlanValues) => {
    setError(null);
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, ...toInput(values) });
        toast({ title: `${values.name} saved` });
      } else {
        await create.mutateAsync(toInput(values));
        toast({ title: `${values.name} created` });
      }
      onClose();
    } catch (err) {
      const envelope = extractErrorEnvelope(err);
      const errors = envelope.errors ?? {};
      for (const field of PLAN_FIELDS) {
        if (errors[field]) form.setError(field, { message: errors[field].join(" ") });
      }
      if (!PLAN_FIELDS.some((f) => errors[f])) setError(envelope.detail);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{editing ? `Edit ${editing.name}` : "New plan"}</DialogTitle>
          <DialogDescription>
            {editing
              ? "Changes apply at once to every school on this plan that has no override. Lowering a limit keeps existing data; only new additions are refused."
              : "Leave a limit blank for unlimited, and the price blank for priced by quotation."}
          </DialogDescription>
        </DialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input autoFocus placeholder="Pro" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="code"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Code</FormLabel>
                    <FormControl>
                      <Input
                        className="font-mono"
                        placeholder="pro"
                        autoCapitalize="none"
                        spellCheck={false}
                        {...field}
                        onChange={(e) => field.onChange(e.target.value.toLowerCase())}
                      />
                    </FormControl>
                    {editing && (
                      <FormDescription>Schools on this plan stay on it if the code changes.</FormDescription>
                    )}
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="max_students"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Student limit</FormLabel>
                    <FormControl>
                      <Input inputMode="numeric" placeholder="Unlimited" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="max_staff_users"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Staff account limit</FormLabel>
                    <FormControl>
                      <Input inputMode="numeric" placeholder="Unlimited" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="price"
                render={({ field }) => (
                  <FormItem className="sm:col-span-2">
                    <FormLabel>Price per academic session (₦)</FormLabel>
                    <FormControl>
                      <Input inputMode="numeric" placeholder="By quotation" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <FormField
              control={form.control}
              name="features"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Features</FormLabel>
                  <FeatureChecklist value={field.value as FeatureKey[]} onChange={field.onChange} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="is_active"
              render={({ field }) => (
                <FormItem className="flex items-center justify-between gap-4 border-y border-[color:var(--border)] py-3">
                  <div>
                    <FormLabel>Offered</FormLabel>
                    <FormDescription>
                      Off: new schools can't pick it. Schools already on it keep it.
                    </FormDescription>
                  </div>
                  <FormControl>
                    <Switch checked={field.value} onCheckedChange={field.onChange} />
                  </FormControl>
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" variant="brand" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting ? "Saving…" : editing ? "Save plan" : "Create plan"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

function DeletePlanDialog({ plan, onClose }: { plan: PlanRow | null; onClose: () => void }) {
  const remove = useDeletePlan();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (plan) setError(null);
  }, [plan]);

  const onDelete = async () => {
    if (!plan) return;
    setError(null);
    try {
      await remove.mutateAsync(plan.id);
      toast({ title: `${plan.name} deleted` });
      onClose();
    } catch (err) {
      setError(extractErrorEnvelope(err).detail);
    }
  };

  return (
    <Dialog open={plan !== null} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {plan?.name}?</DialogTitle>
          <DialogDescription>
            No school is on this plan, so it can be removed for good. To keep it on record
            but stop offering it, archive it instead.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="destructive" disabled={remove.isPending} onClick={onDelete}>
            {remove.isPending ? "Deleting…" : "Delete plan"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
