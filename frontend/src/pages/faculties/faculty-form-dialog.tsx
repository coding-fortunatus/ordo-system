import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
  type Faculty,
  type FacultyInput,
  useCreateFaculty,
  useUpdateFaculty,
} from "@/api/faculties";
import { useDepartments } from "@/api/departments";
import { PortalLockedHint, portalLocked } from "@/components/from-portal-badge";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";

const schema = z.object({
  name: z.string().trim().min(1, "Name is required"),
  slug: z
    .string()
    .trim()
    .min(1, "Code is required")
    .max(50)
    .regex(/^[A-Za-z0-9_-]+$/, "Use letters, numbers, hyphens, or underscores"),
  department_ids: z.array(z.number()),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial: Faculty | null;
}

export function FacultyFormDialog({ open, onOpenChange, initial }: Props) {
  const isEdit = !!initial;
  const [topError, setTopError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", slug: "", department_ids: [] },
  });

  const create = useCreateFaculty();
  const update = useUpdateFaculty(initial?.slug ?? "");
  const departmentsQ = useDepartments({
    all: true,
    enabled: open,
  });
  const allDepartments = useMemo(
    () => departmentsQ.data?.results ?? [],
    [departmentsQ.data],
  );

  const initialId = initial?.id ?? null;
  const selectableDepartments = useMemo(() => {
    return allDepartments.filter(
      (d) => d.faculty === null || d.faculty === initialId,
    );
  }, [allDepartments, initialId]);

  useEffect(() => {
    if (open) {
      form.reset(
        initial
          ? {
              name: initial.name,
              slug: initial.slug,
              department_ids: initial.departments.map((d) => d.id),
            }
          : { name: "", slug: "", department_ids: [] },
      );
      setTopError(null);
    }
  }, [open, initial, form]);

  const onSubmit = async (values: FormValues) => {
    setTopError(null);
    const payload: FacultyInput = {
      name: values.name.trim(),
      slug: values.slug.trim().toUpperCase(),
      department_ids: values.department_ids,
    };
    try {
      if (isEdit) {
        await update.mutateAsync(payload);
        toast({ title: "Faculty updated" });
      } else {
        await create.mutateAsync(payload);
        toast({ title: "Faculty created" });
      }
      onOpenChange(false);
    } catch (err) {
      const envelope = extractErrorEnvelope(err);
      setTopError(envelope.detail);
      if (envelope.errors) {
        for (const [field, messages] of Object.entries(envelope.errors)) {
          if (field === "name" || field === "slug") {
            form.setError(field, { message: messages.join(", ") });
          }
        }
      }
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? "Edit faculty" : "New faculty"}
          </DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update the faculty details and attached departments."
              : "Create a new faculty and optionally attach departments."}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
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
                  <FormControl>
                    <Input
                      placeholder="Faculty of Science"
                      autoComplete="off"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="slug"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Code</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="SCI"
                      autoComplete="off"
                      {...field}
                      onChange={(e) =>
                        field.onChange(e.target.value.toUpperCase())
                      }
                      disabled={portalLocked(initial, "slug")}
                    />
                  </FormControl>
                  <PortalLockedHint locked={portalLocked(initial, "slug")} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="department_ids"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Departments</FormLabel>
                  <FormControl>
                    <div className="rounded-md border border-[color:var(--border)] bg-[color:var(--muted)]/20 max-h-56 overflow-y-auto divide-y divide-[color:var(--border)]/60">
                      {departmentsQ.isLoading ? (
                        <p className="px-3 py-3 font-mono text-[12px] text-muted-foreground">
                          Loading departments…
                        </p>
                      ) : selectableDepartments.length === 0 ? (
                        <p className="px-3 py-3 font-mono text-[12px] text-muted-foreground">
                          No unattached departments. Detach them from their
                          current faculty first.
                        </p>
                      ) : (
                        selectableDepartments.map((d) => {
                          const checked = field.value.includes(d.id);
                          // The portal sets this department's faculty.
                          const fixed = portalLocked(d, "faculty");
                          return (
                            <label
                              key={d.id}
                              className="flex cursor-pointer items-center gap-3 px-3 py-2 text-sm hover:bg-[color:var(--muted)]/50 has-disabled:cursor-not-allowed has-disabled:opacity-60"
                              title={fixed ? "This department's faculty comes from your school portal." : undefined}
                            >
                              <input
                                type="checkbox"
                                className="h-4 w-4 accent-[color:var(--primary)]"
                                checked={checked}
                                disabled={fixed}
                                onChange={(e) => {
                                  if (e.target.checked) {
                                    field.onChange([...field.value, d.id]);
                                  } else {
                                    field.onChange(
                                      field.value.filter((id) => id !== d.id),
                                    );
                                  }
                                }}
                              />
                              <span className="font-mono text-[11px] tracking-wide text-muted-foreground">
                                {d.slug}
                              </span>
                              <span className="font-serif text-[0.95rem]">
                                {d.name}
                              </span>
                            </label>
                          );
                        })
                      )}
                    </div>
                  </FormControl>
                  <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                    {field.value.length} selected
                  </p>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
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
