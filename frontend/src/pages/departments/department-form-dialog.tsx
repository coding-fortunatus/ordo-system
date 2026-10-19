import { useEffect } from "react";
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  type Department,
  type DepartmentInput,
  useCreateDepartment,
  useUpdateDepartment,
} from "@/api/departments";
import { useFaculties } from "@/api/faculties";
import { PortalLockedHint, portalLocked } from "@/components/from-portal-badge";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import { useState } from "react";

const NO_FACULTY = "__none__";

const schema = z.object({
  name: z.string().trim().min(1, "Name is required"),
  slug: z
    .string()
    .trim()
    .min(1, "Code is required")
    .max(50)
    .regex(/^[A-Za-z0-9_-]+$/, "Use letters, numbers, hyphens, or underscores"),
  faculty: z.number().nullable(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial: Department | null;
}

export function DepartmentFormDialog({ open, onOpenChange, initial }: Props) {
  const isEdit = !!initial;
  const [topError, setTopError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", slug: "", faculty: null },
  });

  useEffect(() => {
    if (open) {
      form.reset(
        initial
          ? {
              name: initial.name,
              slug: initial.slug,
              faculty: initial.faculty,
            }
          : { name: "", slug: "", faculty: null },
      );
      setTopError(null);
    }
  }, [open, initial, form]);

  const locked = (field: string) => portalLocked(initial, field);

  const create = useCreateDepartment();
  const update = useUpdateDepartment(initial?.slug ?? "");
  const facultiesQ = useFaculties({ all: true, enabled: open });
  const faculties = facultiesQ.data?.results ?? [];

  const onSubmit = async (values: FormValues) => {
    setTopError(null);
    const payload: DepartmentInput = {
      name: values.name.trim(),
      slug: values.slug.trim().toUpperCase(),
      faculty: values.faculty,
    };
    try {
      if (isEdit) {
        await update.mutateAsync(payload);
        toast({ title: "Department updated" });
      } else {
        await create.mutateAsync(payload);
        toast({ title: "Department created" });
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {isEdit ? "Edit department" : "New department"}
          </DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update the department name or code."
              : "Add a new department to the system."}
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
                      placeholder="Computer Science"
                      autoComplete="off"
                      {...field}
                      disabled={locked("name")}
                    />
                  </FormControl>
                  <PortalLockedHint locked={locked("name")} />
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
                      placeholder="CS"
                      autoComplete="off"
                      {...field}
                      onChange={(e) =>
                        field.onChange(e.target.value.toUpperCase())
                      }
                      disabled={locked("slug")}
                    />
                  </FormControl>
                  <PortalLockedHint locked={locked("slug")} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="faculty"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Faculty</FormLabel>
                  <Select
                    value={field.value ? String(field.value) : NO_FACULTY}
                    onValueChange={(v) =>
                      field.onChange(v === NO_FACULTY ? null : Number(v))
                    }
                    disabled={locked("faculty")}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Unassigned" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value={NO_FACULTY}>Unassigned</SelectItem>
                      {faculties.map((f) => (
                        <SelectItem key={f.id} value={String(f.id)}>
                          {f.slug} · {f.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <PortalLockedHint locked={locked("faculty")} />
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
