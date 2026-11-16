import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useHasFeature } from "@/api/school";
import {
  useEnableBulkUpload,
  useResetSystem,
  useSystemSettings,
  useUpdateSystemSettings,
  type ResetScope,
} from "@/api/system";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import { useConfirm } from "@/lib/confirm";
import { PageHeader } from "@/components/layout/page-header";
import { SupportAccessCard } from "./support-access";

const schema = z.object({
  institution_name: z.string().trim().max(255).default(""),
  institution_short_name: z.string().trim().max(50).default(""),
  exam_heading: z.string().trim().max(255).default(""),
  institution_address: z.string().trim().default(""),
  contact_email: z
    .string()
    .trim()
    .email("Enter a valid email")
    .or(z.literal(""))
    .default(""),
  contact_phone: z.string().trim().max(50).default(""),
  brand_color: z
    .string()
    .trim()
    .regex(/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/, "Use a hex like #3FE06B")
    .or(z.literal(""))
    .default(""),
  session: z.string().trim().min(1, "Session is required"),
  semester: z.string().trim().min(1, "Semester is required"),
});

type Values = z.infer<typeof schema>;

export default function SettingsPage() {
  const settings = useSystemSettings();
  const update = useUpdateSystemSettings();
  const reset = useResetSystem();
  const enable = useEnableBulkUpload();
  const confirm = useConfirm();

  const [topError, setTopError] = useState<string | null>(null);
  const [logoFile, setLogoFile] = useState<File | null>(null);
  // Without branding in the plan the logo, exam heading and colour are locked
  // (the server refuses changes to them).
  const branding = useHasFeature("branding");
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      institution_name: "",
      institution_short_name: "",
      exam_heading: "",
      institution_address: "",
      contact_email: "",
      contact_phone: "",
      brand_color: "",
      session: "",
      semester: "",
    },
  });

  useEffect(() => {
    if (settings.data) {
      form.reset({
        institution_name: settings.data.institution_name ?? "",
        institution_short_name: settings.data.institution_short_name ?? "",
        exam_heading: settings.data.exam_heading ?? "",
        institution_address: settings.data.institution_address ?? "",
        contact_email: settings.data.contact_email ?? "",
        contact_phone: settings.data.contact_phone ?? "",
        brand_color: settings.data.brand_color ?? "",
        session: settings.data.session,
        semester: settings.data.semester,
      });
    }
  }, [settings.data, form]);

  // Prefer a freshly-picked file's preview, else the saved logo.
  const logoPreview = useMemo(
    () => (logoFile ? URL.createObjectURL(logoFile) : settings.data?.logo_url ?? null),
    [logoFile, settings.data?.logo_url],
  );
  useEffect(() => {
    return () => {
      if (logoFile && logoPreview) URL.revokeObjectURL(logoPreview);
    };
  }, [logoFile, logoPreview]);

  const onSubmit = async (v: Values) => {
    setTopError(null);
    try {
      await update.mutateAsync({ ...v, logo: logoFile ?? undefined });
      setLogoFile(null);
      toast({ title: "Settings updated" });
    } catch (err) {
      setTopError(extractErrorEnvelope(err).detail);
    }
  };

  const runReset = async (
    scope: ResetScope,
    opts: { title: string; description: string; success: string },
  ) => {
    const ok = await confirm({
      title: opts.title,
      description: opts.description,
      confirmLabel: "Reset",
      destructive: true,
    });
    if (!ok) return;
    try {
      await reset.mutateAsync(scope);
      toast({ title: opts.success });
    } catch (err) {
      toast({
        title: "Reset failed",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  const onReset = () =>
    runReset("all", {
      title: "Reset the system?",
      description:
        "Every department, class, course, hall, student, timetable and seat allocation will be deleted. This cannot be undone.",
      success: "System reset complete",
    });

  const onResetTimetable = () =>
    runReset("timetable", {
      title: "Reset the timetable?",
      description:
        "The timetable, distribution and seat allocations will all be deleted because they depend on the timetable. Departments, classes, courses, halls and students are kept, and uploads are unlocked. This cannot be undone.",
      success: "Timetable, distribution and allocation cleared",
    });

  const onResetDistribution = () =>
    runReset("distribution", {
      title: "Reset the distribution?",
      description:
        "The distribution and seat allocations will be deleted because the allocation depends on the distribution. The timetable is kept. This cannot be undone.",
      success: "Distribution and allocation cleared",
    });

  const onResetAllocation = () =>
    runReset("allocation", {
      title: "Reset the allocation?",
      description:
        "All seat allocations will be deleted. The timetable and distribution are kept. This cannot be undone.",
      success: "Seat allocations cleared",
    });

  const onEnableBulk = async () => {
    try {
      await enable.mutateAsync();
      toast({ title: "Bulk upload enabled" });
    } catch (err) {
      toast({
        title: "Could not enable",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <div className="space-y-10">
      <PageHeader
        section="Admin · Settings"
        title="Settings"
        description="Set your institution's name and logo, choose the current session and semester, and reset data when you need a fresh start."
      />

      <Card>
        <CardHeader>
          <CardTitle>System configuration</CardTitle>
          <CardDescription>
            Institution branding and the active session/semester. The logo and
            these labels appear in the app beside the Ordo mark and on
            every exported document.
          </CardDescription>
        </CardHeader>
        {settings.isLoading ? (
          <CardContent>
            <Skeleton className="h-32 w-full" />
          </CardContent>
        ) : (
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)}>
              <CardContent className="space-y-4">
                {topError && (
                  <Alert variant="destructive">
                    <AlertDescription>{topError}</AlertDescription>
                  </Alert>
                )}

                {!branding && (
                  <Alert>
                    <AlertDescription>
                      Custom branding isn't in your school's plan, so the logo, exam heading
                      and brand colour can't be changed. Documents show your institution's
                      name and contact details without them.
                    </AlertDescription>
                  </Alert>
                )}

                {/* Logo upload + live preview */}
                <div className="flex items-center gap-4">
                  <div className="grid size-16 shrink-0 place-items-center overflow-hidden rounded-md border border-input bg-muted/30">
                    {logoPreview ? (
                      <img
                        src={logoPreview}
                        alt="Institution logo"
                        className="size-full object-contain"
                      />
                    ) : (
                      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                        No logo
                      </span>
                    )}
                  </div>
                  <div className="space-y-1">
                    <FormLabel>Institution logo</FormLabel>
                    <Input
                      type="file"
                      disabled={!branding}
                      accept="image/png,image/jpeg,image/svg+xml,image/webp"
                      onChange={(e) =>
                        setLogoFile(e.target.files?.[0] ?? null)
                      }
                    />
                    <p className="text-xs text-muted-foreground">
                      PNG or JPG. Used on documents and beside the app logo.
                    </p>
                  </div>
                </div>

                <FormField
                  control={form.control}
                  name="institution_name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Institution name</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="Institute of Business and Research"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormField
                    control={form.control}
                    name="institution_short_name"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Short name / acronym</FormLabel>
                        <FormControl>
                          <Input placeholder="IBR" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="exam_heading"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Exam heading line</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="Office of the Examinations Committee"
                            disabled={!branding}
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
                <FormField
                  control={form.control}
                  name="institution_address"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Address</FormLabel>
                      <FormControl>
                        <textarea
                          rows={2}
                          placeholder="123 Campus Road, City"
                          className="flex min-h-[64px] w-full border border-transparent border-b-input bg-transparent px-0 py-2 text-base outline-none transition-[color,border-color] placeholder:text-muted-foreground focus-visible:border-b-ring aria-invalid:border-b-destructive md:text-sm"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormField
                    control={form.control}
                    name="contact_email"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Contact email</FormLabel>
                        <FormControl>
                          <Input
                            type="email"
                            placeholder="exams@ibr.edu"
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="contact_phone"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Contact phone</FormLabel>
                        <FormControl>
                          <Input placeholder="+234 ..." {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>

                <FormField
                  control={form.control}
                  name="brand_color"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Brand color</FormLabel>
                      <div className="flex items-center gap-3">
                        <input
                          type="color"
                          aria-label="Brand color"
                          value={field.value || "#3FE06B"}
                          disabled={!branding}
                          onChange={(e) => field.onChange(e.target.value)}
                          className="h-9 w-12 shrink-0 cursor-pointer rounded-none border border-input bg-transparent p-1"
                        />
                        <FormControl>
                          <Input
                            placeholder="#3FE06B"
                            className="max-w-[160px]"
                            disabled={!branding}
                            {...field}
                          />
                        </FormControl>
                        {field.value && branding && (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => field.onChange("")}
                          >
                            Reset to default
                          </Button>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Drives the app's primary accent across light and dark
                        mode. Leave blank for the default Signal green.
                      </p>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormField
                    control={form.control}
                    name="session"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Academic session</FormLabel>
                        <FormControl>
                          <Input placeholder="2024/2025" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="semester"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Semester</FormLabel>
                        <Select
                          value={field.value}
                          onValueChange={field.onChange}
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="Select semester" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            <SelectItem value="First Semester">
                              First Semester
                            </SelectItem>
                            <SelectItem value="Second Semester">
                              Second Semester
                            </SelectItem>
                            <SelectItem value="Third Semester">
                              Third Semester
                            </SelectItem>
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
              </CardContent>
              <CardFooter className="justify-end">
                <Button type="submit" disabled={form.formState.isSubmitting}>
                  {form.formState.isSubmitting ? "Saving…" : "Save changes"}
                </Button>
              </CardFooter>
            </form>
          </Form>
        )}
      </Card>

      <SupportAccessCard />

      <Card>
        <CardHeader>
          <CardTitle>Upload lock</CardTitle>
          <CardDescription>
            Once a timetable is generated, file uploads are locked. Re-enable to
            modify departments, classes, courses, or students.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {settings.data?.has_timetable ? (
            <Alert>
              <AlertTitle>Uploads currently locked</AlertTitle>
              <AlertDescription>
                A timetable already exists. Click below if you need to allow
                further uploads.
              </AlertDescription>
            </Alert>
          ) : (
            <p className="text-sm text-muted-foreground">
              Uploads are open. Generating a timetable will lock them
              automatically.
            </p>
          )}
        </CardContent>
        <CardFooter className="justify-end">
          <Button
            variant="outline"
            onClick={onEnableBulk}
            disabled={enable.isPending || !settings.data?.has_timetable}
          >
            Re-enable uploads
          </Button>
        </CardFooter>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Reset schedule data</CardTitle>
          <CardDescription>
            Clear generated scheduling data without touching departments,
            classes, courses, halls or students. Each level also clears the
            levels that depend on it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="font-medium">Allocation only</p>
              <p className="text-muted-foreground">
                Removes seat allocations. Timetable and distribution are kept.
              </p>
            </div>
            <Button
              variant="outline"
              onClick={onResetAllocation}
              disabled={reset.isPending}
            >
              Reset allocation
            </Button>
          </div>
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="font-medium">Distribution</p>
              <p className="text-muted-foreground">
                Removes the distribution and seat allocations. Timetable is
                kept.
              </p>
            </div>
            <Button
              variant="outline"
              onClick={onResetDistribution}
              disabled={reset.isPending}
            >
              Reset distribution
            </Button>
          </div>
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="font-medium">Timetable</p>
              <p className="text-muted-foreground">
                Removes the timetable, distribution and seat allocations, and
                unlocks uploads.
              </p>
            </div>
            <Button
              variant="outline"
              onClick={onResetTimetable}
              disabled={reset.isPending}
            >
              Reset timetable
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card className="border-[color:var(--accent-red-fg)]/30 bg-[color:var(--accent-red)]/30">
        <CardHeader>
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[color:var(--accent-red-fg)]">
            Danger zone
          </p>
          <CardTitle className="font-serif text-[1.5rem] tracking-tight text-[color:var(--accent-red-fg)]">
            Reset everything
          </CardTitle>
          <CardDescription className="text-[color:var(--accent-red-fg)]/70">
            Wipe all departments, classes, courses, halls, students, timetables
            and seat allocations. This cannot be undone.
          </CardDescription>
        </CardHeader>
        <CardFooter className="justify-end">
          <Button
            variant="destructive"
            onClick={onReset}
            disabled={reset.isPending}
          >
            {reset.isPending ? "Resetting…" : "Reset everything"}
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
