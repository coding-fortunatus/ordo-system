import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { KeyRound, Unplug } from "lucide-react";
import {
  type PortalConnection,
  useDeleteConnection,
  useSaveConnection,
} from "@/api/portal-sync";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import {
  Form,
  FormControl,
  FormDescription,
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
import { extractErrorEnvelope } from "@/lib/api";
import { useConfirm } from "@/lib/confirm";
import { toast } from "@/lib/use-toast";

const schema = z
  .object({
    mode: z.enum(["per_type", "combined"]),
    auth_type: z.enum(["api_key_header", "bearer"]),
    header_name: z.string().trim().max(100),
    secret: z.string().trim(),
  })
  .superRefine((v, ctx) => {
    if (v.auth_type === "api_key_header" && !v.header_name) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["header_name"],
        message: "Give the header name the portal expects",
      });
    }
  });

type Values = z.infer<typeof schema>;

interface Props {
  connection: PortalConnection | null;
}

/** Where Ordo calls and how it signs in. The key is write only (AC-1). */
export function ConnectionCard({ connection }: Props) {
  const save = useSaveConnection();
  const remove = useDeleteConnection();
  const confirm = useConfirm();
  const [topError, setTopError] = useState<string | null>(null);
  const isNew = !connection;

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      mode: "per_type",
      auth_type: "api_key_header",
      header_name: "X-API-Key",
      secret: "",
    },
  });

  useEffect(() => {
    if (connection) {
      form.reset({
        mode: connection.mode,
        auth_type: connection.auth_type,
        header_name: connection.header_name,
        secret: "",
      });
    }
  }, [connection, form]);

  const authType = form.watch("auth_type");
  // The server refuses a mode change while endpoints exist.
  const modeLocked = !!connection?.endpoints.length;

  const onSubmit = async (v: Values) => {
    setTopError(null);
    if (isNew && !v.secret) {
      form.setError("secret", { message: "Give the portal's key" });
      return;
    }
    try {
      await save.mutateAsync({
        mode: v.mode,
        auth_type: v.auth_type,
        header_name: v.auth_type === "api_key_header" ? v.header_name : "",
        ...(v.secret ? { secret: v.secret } : {}),
      });
      form.setValue("secret", "");
      toast({ title: isNew ? "Portal connected" : "Connection saved" });
    } catch (err) {
      const env = extractErrorEnvelope(err);
      for (const [field, messages] of Object.entries(env.errors ?? {})) {
        if (field in form.getValues()) {
          form.setError(field as keyof Values, { message: messages[0] });
        }
      }
      setTopError(env.detail);
    }
  };

  const onDisconnect = async () => {
    const ok = await confirm({
      title: "Disconnect your portal?",
      description:
        "Ordo stops syncing. Records the portal synced stay, but become Ordo's own: editable here, and no longer updated or deleted by the portal. The endpoints, their mappings and the saved key are deleted. Sync history stays.",
      confirmLabel: "Disconnect",
      destructive: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync();
      form.reset();
      toast({ title: "Portal disconnected" });
    } catch (err) {
      toast({
        title: "Could not disconnect",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-3">
          Connection
          {connection && (
            <Badge variant={connection.approved ? "secondary" : "warning"}>
              {connection.approved ? "First sync approved" : "Next sync is a preview"}
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          How Ordo signs in to your portal. The key is stored encrypted and is
          never shown again, not even to you.
        </CardDescription>
      </CardHeader>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)}>
          <CardContent className="space-y-5">
            {topError && (
              <Alert variant="destructive">
                <AlertDescription>{topError}</AlertDescription>
              </Alert>
            )}
            <FormField
              control={form.control}
              name="mode"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Your portal sends</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange} disabled={modeLocked}>
                    <FormControl>
                      <SelectTrigger className="w-full sm:w-[360px]">
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="per_type">One address per data type</SelectItem>
                      <SelectItem value="combined">One address for everything</SelectItem>
                    </SelectContent>
                  </Select>
                  <FormDescription>
                    {modeLocked
                      ? "Remove the endpoints below to change this."
                      : field.value === "combined"
                        ? "One list of students that also names each one's department, faculty, class and courses."
                        : "Separate addresses for departments, classes, courses and students. Only students is required."}
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="auth_type"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Sign in with</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="api_key_header">An API key header</SelectItem>
                        <SelectItem value="bearer">A bearer token</SelectItem>
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {authType === "api_key_header" ? (
                <FormField
                  control={form.control}
                  name="header_name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Header name</FormLabel>
                      <FormControl>
                        <Input placeholder="X-API-Key" autoComplete="off" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              ) : (
                <div className="self-end pb-2 text-[13px] text-muted-foreground">
                  Sent as <code className="font-mono">Authorization: Bearer …</code>
                </div>
              )}
            </div>

            <FormField
              control={form.control}
              name="secret"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{isNew ? "Key" : "Replace key"}</FormLabel>
                  <FormControl>
                    <PasswordInput
                      autoComplete="new-password"
                      placeholder={
                        connection?.has_secret
                          ? `••••••••${connection.secret_last4}`
                          : "Paste the key your portal gave you"
                      }
                      {...field}
                    />
                  </FormControl>
                  <FormDescription className="flex items-center gap-1.5">
                    <KeyRound className="size-3.5" strokeWidth={2} aria-hidden />
                    {connection?.has_secret
                      ? "Leave blank to keep the saved key."
                      : "Ordo sends it with every request to your portal."}
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          </CardContent>
          <CardFooter className="flex-wrap justify-end gap-3">
            {connection && (
              <Button
                type="button"
                variant="ghost"
                className="mr-auto text-destructive hover:text-destructive"
                onClick={onDisconnect}
                disabled={remove.isPending || save.isPending}
              >
                <Unplug data-icon="inline-start" strokeWidth={2} />
                {remove.isPending ? "Disconnecting…" : "Disconnect"}
              </Button>
            )}
            <Button type="submit" disabled={save.isPending || remove.isPending}>
              {save.isPending ? "Saving…" : isNew ? "Connect portal" : "Save connection"}
            </Button>
          </CardFooter>
        </form>
      </Form>
    </Card>
  );
}
