import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  type Course,
  useCreateCourse,
  useUpdateCourse,
} from "@/api/courses";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { PortalLockedHint, portalLocked } from "@/components/from-portal-badge";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";

const schema = z.object({
  name: z.string().trim().min(1, "Title is required"),
  code: z.string().trim().min(1, "Code is required"),
  exam_type: z.enum(["PBE", "CBE"]),
});

type Values = z.infer<typeof schema>;

interface Props {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  initial: Course | null;
}

export function CourseFormDialog({ open, onOpenChange, initial }: Props) {
  const isEdit = !!initial;
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", code: "", exam_type: "PBE" },
  });
  const locked = (field: string) => portalLocked(initial, field);
  const create = useCreateCourse();
  const update = useUpdateCourse(initial?.id ?? 0);

  useEffect(() => {
    if (open) {
      form.reset(
        initial
          ? { name: initial.name, code: initial.code, exam_type: initial.exam_type }
          : { name: "", code: "", exam_type: "PBE" },
      );
      setTopError(null);
    }
  }, [open, initial, form]);

  const onSubmit = async (values: Values) => {
    setTopError(null);
    const payload = {
      name: values.name.trim(),
      code: values.code.trim().toUpperCase(),
      exam_type: values.exam_type,
    };
    try {
      if (isEdit) {
        await update.mutateAsync(payload);
        toast({ title: "Course updated" });
      } else {
        await create.mutateAsync(payload);
        toast({ title: "Course created" });
      }
      onOpenChange(false);
    } catch (err) {
      const env = extractErrorEnvelope(err);
      setTopError(env.detail);
      if (env.errors) {
        for (const [k, v] of Object.entries(env.errors)) {
          if (k === "name" || k === "code" || k === "exam_type") {
            form.setError(k, { message: v.join(", ") });
          }
        }
      }
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit course" : "New course"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update the course title, code, or exam type."
              : "Add a new course to the global catalog."}
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
              name="code"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Code</FormLabel>
                  <FormControl>
                    <Input
                      autoComplete="off"
                      placeholder="CSC101"
                      {...field}
                      onChange={(e) =>
                        field.onChange(e.target.value.toUpperCase())
                      }
                      disabled={locked("code")}
                    />
                  </FormControl>
                  <PortalLockedHint locked={locked("code")} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Title</FormLabel>
                  <FormControl>
                    <Input
                      autoComplete="off"
                      placeholder="Introduction to Computing"
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
              name="exam_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Exam type</FormLabel>
                  <Select
                    value={field.value}
                    onValueChange={field.onChange}
                    disabled={locked("exam_type")}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="PBE">PBE (paper)</SelectItem>
                      <SelectItem value="CBE">CBE (computer)</SelectItem>
                    </SelectContent>
                  </Select>
                  <PortalLockedHint locked={locked("exam_type")} />
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
