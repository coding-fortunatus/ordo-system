import { useMemo, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, Database, Globe, Mail } from "lucide-react";
import {
  FEATURE_LABELS,
  type CreateSchoolInput,
  type FeatureKey,
  type SchoolDetail,
  usePlans,
  useCreateSchool,
  useUpdateSchool,
} from "@/api/platform";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { DatePicker } from "@/components/ui/date-picker";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";

// Same rule as the server (tenancy.slugs.SLUG_RE); the server also checks
// reserved and taken addresses.
const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])$/;
const FEATURE_KEYS = Object.keys(FEATURE_LABELS) as FeatureKey[];
const DEFAULT_TIMEZONE = "Africa/Lagos";

/** A suggested address from the school's name: "University of Lagos" → "university-of-lagos". */
function slugify(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 30)
    .replace(/-+$/, "");
}

const optionalCount = z
  .string()
  .trim()
  .regex(/^\d*$/, "Use a whole number, or leave it blank");

const schema = z
  .object({
    name: z.string().trim().min(1, "Name is required").max(255),
    slug: z
      .string()
      .trim()
      .regex(SLUG_RE, "3 to 30 lowercase letters, digits or hyphens, not starting or ending with a hyphen"),
    first_admin_name: z.string().trim().min(1, "Name is required"),
    first_admin_email: z.string().trim().email("Enter a valid email"),
    plan: z.string().min(1, "Pick a plan"),
    max_students_override: optionalCount,
    max_staff_users_override: optionalCount,
    use_plan_features: z.boolean(),
    features_override: z.array(z.enum(["seat_allocation", "exports", "publication", "branding", "portal_sync"])),
    plan_started_on: z.string(),
    renews_on: z.string(),
    contact_name: z.string().trim().max(255),
    contact_email: z.string().trim().email("Enter a valid email").or(z.literal("")),
    country: z
      .string()
      .trim()
      .regex(/^([a-zA-Z]{2})?$/, "Use a two letter code, like NG"),
    timezone: z.string().trim().min(1, "Pick a time zone"),
    notes: z.string(),
  })
  .refine((v) => !v.plan_started_on || !v.renews_on || v.renews_on >= v.plan_started_on, {
    path: ["renews_on"],
    message: "The renewal date can't be before the plan starts",
  });

type Values = z.infer<typeof schema>;

const FIELDS = [
  "name",
  "slug",
  "first_admin_name",
  "first_admin_email",
  "plan",
  "max_students_override",
  "max_staff_users_override",
  "features_override",
  "plan_started_on",
  "renews_on",
  "contact_name",
  "contact_email",
  "country",
  "timezone",
  "notes",
] as const;

const count = (value: string) => (value === "" ? null : Number(value));
const text = (value: number | null) => (value === null ? "" : String(value));

function toValues(school: SchoolDetail | undefined): Values {
  if (!school) {
    return {
      name: "",
      slug: "",
      first_admin_name: "",
      first_admin_email: "",
      plan: "",
      max_students_override: "",
      max_staff_users_override: "",
      use_plan_features: true,
      features_override: [],
      plan_started_on: "",
      renews_on: "",
      contact_name: "",
      contact_email: "",
      country: "NG",
      timezone: DEFAULT_TIMEZONE,
      notes: "",
    };
  }
  return {
    name: school.name,
    slug: school.slug,
    first_admin_name: school.first_admin_name,
    first_admin_email: school.first_admin_email,
    plan: school.plan.code,
    max_students_override: text(school.max_students_override),
    max_staff_users_override: text(school.max_staff_users_override),
    use_plan_features: school.features_override === null,
    features_override: school.features_override ?? [],
    plan_started_on: school.plan_started_on ?? "",
    renews_on: school.renews_on ?? "",
    contact_name: school.contact_name,
    contact_email: school.contact_email,
    country: school.country,
    timezone: school.timezone,
    notes: school.notes,
  };
}

function toInput(v: Values): CreateSchoolInput {
  return {
    name: v.name,
    slug: v.slug,
    plan: v.plan,
    first_admin_name: v.first_admin_name,
    first_admin_email: v.first_admin_email,
    max_students_override: count(v.max_students_override),
    max_staff_users_override: count(v.max_staff_users_override),
    features_override: v.use_plan_features ? null : v.features_override,
    plan_started_on: v.plan_started_on || null,
    renews_on: v.renews_on || null,
    contact_name: v.contact_name,
    contact_email: v.contact_email,
    country: v.country.toUpperCase(),
    timezone: v.timezone,
    notes: v.notes,
  };
}

const TEXTAREA =
  "flex min-h-[72px] w-full border border-transparent border-b-input bg-transparent px-0 py-2 text-base outline-none transition-[color,border-color] placeholder:text-muted-foreground focus-visible:border-b-ring aria-invalid:border-b-destructive md:text-sm";

/**
 * Create a school, or edit one when ``school`` is given. Editing can change
 * anything but the address; a school may stay on a plan that is no longer
 * offered, but can't be moved onto one.
 */
export function SchoolForm({ school }: { school?: SchoolDetail }) {
  const navigate = useNavigate();
  const plans = usePlans();
  const create = useCreateSchool();
  const update = useUpdateSchool();
  const editing = school !== undefined;
  const [topError, setTopError] = useState<string | null>(null);
  // The address follows the name until someone edits it by hand. It never
  // changes once the school exists.
  const [slugTouched, setSlugTouched] = useState(editing);
  const offered = useMemo(
    () => plans.data?.filter((p) => p.is_active || p.code === school?.plan.code) ?? [],
    [plans.data, school?.plan.code],
  );
  const zones = useMemo(() => {
    try {
      return Intl.supportedValuesOf("timeZone");
    } catch {
      return [DEFAULT_TIMEZONE];
    }
  }, []);
  // The console lives at <console>.<base>; schools at <slug>.<base>.
  const base = typeof window === "undefined" ? "" : window.location.hostname.replace(/^[^.]+\./, "");

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: toValues(school),
  });
  const slug = form.watch("slug");
  const usePlanFeatures = form.watch("use_plan_features");
  const plan = offered.find((p) => p.code === form.watch("plan"));

  const onSubmit = async (values: Values) => {
    setTopError(null);
    try {
      if (school) {
        const { slug: _slug, ...input } = toInput(values);
        await update.mutateAsync({ id: school.id, ...input });
        toast({ title: `${values.name} saved` });
        navigate({ to: "/console/schools/$id", params: { id: String(school.id) } });
        return;
      }
      const created = await create.mutateAsync(toInput(values));
      toast({
        title: `Setting up ${created.name}`,
        description: `${values.first_admin_email} gets an invite as soon as it's ready.`,
      });
      navigate({ to: "/console/schools/$id", params: { id: String(created.id) } });
    } catch (err) {
      const envelope = extractErrorEnvelope(err);
      const errors = envelope.errors ?? {};
      for (const field of FIELDS) {
        if (errors[field]) form.setError(field, { message: errors[field].join(" ") });
      }
      if (!FIELDS.some((f) => errors[f])) setTopError(envelope.detail);
    }
  };

  return (
    <div className="space-y-8">
      {school ? (
        <Link
          to="/console/schools/$id"
          params={{ id: String(school.id) }}
          className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" aria-hidden />
          {school.name}
        </Link>
      ) : (
        <Link
          to="/console/schools"
          className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" aria-hidden />
          Schools
        </Link>
      )}
      {school ? (
        <PageHeader
          section="Console · Edit school"
          title={`Edit ${school.name}.`}
          description="Changes apply at once. The address can never change."
        />
      ) : (
        <PageHeader
          section="Console · New school"
          title="Add a school."
          description="Ordo builds the school its own private database in the background, then emails the first administrator a link to set their password."
        />
      )}

      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_300px]"
          noValidate
        >
          <div className="space-y-6">
            {topError && (
              <Alert variant="destructive" role="alert">
                <AlertDescription>{topError}</AlertDescription>
              </Alert>
            )}

            <Section title="School" description="The name shows on its dashboard and documents. The address can never change.">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input
                        autoFocus={!editing}
                        placeholder="University of Lagos"
                        {...field}
                        onChange={(e) => {
                          field.onChange(e);
                          if (!slugTouched) {
                            form.setValue("slug", slugify(e.target.value), {
                              shouldValidate: form.formState.isSubmitted,
                            });
                          }
                        }}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {!editing && (
                <FormField
                  control={form.control}
                  name="slug"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Address</FormLabel>
                      <div className="flex items-baseline gap-1">
                        <FormControl>
                          <Input
                            className="font-mono"
                            placeholder="unilag"
                            autoCapitalize="none"
                            spellCheck={false}
                            {...field}
                            onChange={(e) => {
                              setSlugTouched(true);
                              field.onChange(e.target.value.toLowerCase());
                            }}
                          />
                        </FormControl>
                        <span className="shrink-0 font-mono text-[13px] text-muted-foreground">.{base}</span>
                      </div>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
            </Section>

            <Section title="First administrator" description="They become the school's Super Admin and can invite everyone else.">
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField
                  control={form.control}
                  name="first_admin_name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Name</FormLabel>
                      <FormControl>
                        <Input placeholder="Ada Obi" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="first_admin_email"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Email</FormLabel>
                      <FormControl>
                        <Input type="email" placeholder="ada@unilag.edu.ng" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            </Section>

            <Section title="Plan" description="Leave a limit blank to use the plan's. An override applies to this school only.">
              <FormField
                control={form.control}
                name="plan"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Plan</FormLabel>
                    <Select value={field.value} onValueChange={(v) => field.onChange(v ?? "")}>
                      <FormControl>
                        <SelectTrigger className="w-full">
                          <SelectValue placeholder={plans.isLoading ? "Loading plans…" : "Pick a plan"} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {offered.map((p) => (
                          <SelectItem key={p.code} value={p.code}>
                            {p.name}
                            {!p.is_active && " (no longer offered)"}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {plan && (
                      <FormDescription>
                        {limit(plan.max_students, "students")} · {limit(plan.max_staff_users, "staff accounts")} ·{" "}
                        {plan.features.length ? plan.features.map((f) => FEATURE_LABELS[f]).join(", ") : "no extra features"}
                      </FormDescription>
                    )}
                    {!plans.isLoading && offered.length === 0 && (
                      <FormDescription>No plan is offered right now.</FormDescription>
                    )}
                    <FormMessage />
                  </FormItem>
                )}
              />
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField
                  control={form.control}
                  name="max_students_override"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Student limit</FormLabel>
                      <FormControl>
                        <Input inputMode="numeric" placeholder="Plan's limit" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="max_staff_users_override"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Staff account limit</FormLabel>
                      <FormControl>
                        <Input inputMode="numeric" placeholder="Plan's limit" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
              <FormField
                control={form.control}
                name="use_plan_features"
                render={({ field }) => (
                  <FormItem className="flex items-center justify-between gap-4 border-y border-[color:var(--border)] py-3">
                    <div>
                      <FormLabel>Use the plan's features</FormLabel>
                      <FormDescription>Turn off to pick this school's features by hand.</FormDescription>
                    </div>
                    <FormControl>
                      <Switch checked={field.value} onCheckedChange={field.onChange} />
                    </FormControl>
                  </FormItem>
                )}
              />
              {!usePlanFeatures && (
                <FormField
                  control={form.control}
                  name="features_override"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Features</FormLabel>
                      <FeatureChecklist value={field.value} onChange={field.onChange} />
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField
                  control={form.control}
                  name="plan_started_on"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Plan starts</FormLabel>
                      <FormControl>
                        <DatePicker clearable {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="renews_on"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Renews</FormLabel>
                      <FormControl>
                        <DatePicker clearable {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            </Section>

            <Section title="Contact and region" description="The contact email goes on the school's documents. Dates and times follow the time zone.">
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField
                  control={form.control}
                  name="contact_name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Contact name</FormLabel>
                      <FormControl>
                        <Input placeholder="Exams office" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="contact_email"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Contact email</FormLabel>
                      <FormControl>
                        <Input type="email" placeholder="exams@unilag.edu.ng" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="country"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Country code</FormLabel>
                      <FormControl>
                        <Input maxLength={2} className="font-mono uppercase" placeholder="NG" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="timezone"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Time zone</FormLabel>
                      <FormControl>
                        <Input list="ordo-time-zones" className="font-mono" spellCheck={false} {...field} />
                      </FormControl>
                      <datalist id="ordo-time-zones">
                        {zones.map((z) => (
                          <option key={z} value={z} />
                        ))}
                      </datalist>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
              <FormField
                control={form.control}
                name="notes"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Notes</FormLabel>
                    <FormControl>
                      <textarea rows={3} className={TEXTAREA} placeholder="Only the Ordo team sees these." {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </Section>

            <div className="flex flex-wrap justify-end gap-2">
              {school ? (
                <Link
                  to="/console/schools/$id"
                  params={{ id: String(school.id) }}
                  className={buttonVariants({ variant: "outline" })}
                >
                  Cancel
                </Link>
              ) : (
                <Link to="/console/schools" className={buttonVariants({ variant: "outline" })}>
                  Cancel
                </Link>
              )}
              <Button type="submit" variant="brand" size="lg" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting
                  ? editing
                    ? "Saving…"
                    : "Creating…"
                  : editing
                    ? "Save changes"
                    : "Create school"}
              </Button>
            </div>
          </div>

          {school ? (
            <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
              <div className="rounded-[12px] border border-[color:var(--border)] bg-card p-5">
                <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                  Lives at
                </p>
                <p className="mt-2 break-all font-mono text-[14px]">{school.domain ?? school.slug}</p>
              </div>
              <p className="rounded-[12px] border border-[color:var(--border)] p-5 text-[13px] leading-[1.55] text-muted-foreground">
                Lowering a limit below what the school already uses is allowed. Its data stays; only
                new students or staff accounts are refused until it's back under the limit.
              </p>
            </aside>
          ) : (
            <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
              <div className="rounded-[12px] border border-[color:var(--border)] bg-card p-5">
                <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                  Will live at
                </p>
                <p className="mt-2 break-all font-mono text-[14px]">
                  {slug || "your-school"}.{base}
                </p>
              </div>
              <ol className="space-y-4 rounded-[12px] border border-[color:var(--border)] p-5">
                <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                  What happens next
                </p>
                <Step icon={<Database className="size-4" />} title="Its own database">
                  A private space for this school's data, kept apart from every other school.
                </Step>
                <Step icon={<Mail className="size-4" />} title="An invite">
                  The first administrator gets a link to set their password. It lasts 7 days and you can resend it.
                </Step>
                <Step icon={<Globe className="size-4" />} title="Live at its address">
                  Usually ready within a minute. If setup fails, you'll see why and can retry.
                </Step>
              </ol>
            </aside>
          )}
        </form>
      </Form>
    </div>
  );
}

/** Tick boxes for each feature key. */
export function FeatureChecklist({
  value,
  onChange,
}: {
  value: FeatureKey[];
  onChange: (next: FeatureKey[]) => void;
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {FEATURE_KEYS.map((key) => {
        const checked = value.includes(key);
        return (
          <label key={key} className="flex items-center gap-2.5 text-[14px]">
            <input
              type="checkbox"
              className="size-4 accent-[color:var(--brand)]"
              checked={checked}
              onChange={() => onChange(checked ? value.filter((k) => k !== key) : [...value, key])}
            />
            {FEATURE_LABELS[key]}
          </label>
        );
      })}
    </div>
  );
}

function limit(value: number | null, noun: string): string {
  return value === null ? `unlimited ${noun}` : `${value.toLocaleString()} ${noun}`;
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">{children}</CardContent>
    </Card>
  );
}

function Step({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span
        className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full"
        style={{ backgroundColor: "var(--brand-soft)", color: "var(--brand-strong)" }}
        aria-hidden
      >
        {icon}
      </span>
      <span>
        <span className="block text-[14px] font-medium">{title}</span>
        <span className="block text-[13px] leading-[1.55] text-muted-foreground">{children}</span>
      </span>
    </li>
  );
}
