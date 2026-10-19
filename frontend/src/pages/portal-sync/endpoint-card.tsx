import { useEffect, useMemo, useState } from "react";
import { Check, FlaskConical, Link2, Trash2 } from "lucide-react";
import {
  type EndpointKind,
  type EndpointTestResult,
  type OrdoField,
  type PortalEndpoint,
  useAddEndpoint,
  useDeleteEndpoint,
  useTestEndpoint,
  useUpdateEndpoint,
} from "@/api/portal-sync";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { extractErrorEnvelope } from "@/lib/api";
import { useConfirm } from "@/lib/confirm";
import { toast } from "@/lib/use-toast";

const NONE = "__none__";
const PREVIEW_ROWS = 5;
const SAMPLE_VALUES = 3;

type Format = PortalEndpoint["format"];
type Paging = PortalEndpoint["pagination"];

const PAGING_LABELS: Record<Paging, string> = {
  none: "One page",
  page_param: "Page number",
  next_link: "Next page link",
};

const COPY: Record<
  EndpointKind,
  { title: string; what: string; rowsPath: string; note?: string }
> = {
  combined: {
    title: "Everything",
    what: "students",
    rowsPath: "data.students",
    note: "Each student row also names its department, faculty, class and course codes. Ordo creates the departments, classes and courses they name; a new course is named by its code until you rename it. Course codes may be a list, or text split by commas or semicolons.",
  },
  departments: {
    title: "Departments",
    what: "departments",
    rowsPath: "data.departments",
    note: "Optional. Faculties come from the faculty column. Without a code column, Ordo makes a code from the name (Computer Science becomes CS), and you can edit it.",
  },
  classes: {
    title: "Classes",
    what: "classes",
    rowsPath: "data.classes",
    note: "Optional. Each class names its department.",
  },
  courses: {
    title: "Courses",
    what: "courses",
    rowsPath: "data.courses",
    note: "Optional. A course may repeat, one row per class it belongs to; the first row sets its title and exam type.",
  },
  students: {
    title: "Students",
    what: "students",
    rowsPath: "data.students",
    note: "Required. Departments and classes a student names that Ordo lacks are created.",
  },
};

interface Props {
  kind: EndpointKind;
  endpoint: PortalEndpoint | null;
  fields: OrdoField[];
}

/** One endpoint: where it is, a test call, and the column mapping. */
export function EndpointCard({ kind, endpoint, fields }: Props) {
  const copy = COPY[kind];
  const add = useAddEndpoint();
  const update = useUpdateEndpoint();
  const remove = useDeleteEndpoint();
  const test = useTestEndpoint();
  const confirm = useConfirm();

  const [url, setUrl] = useState(endpoint?.url ?? "");
  const [format, setFormat] = useState<Format>(endpoint?.format ?? "json");
  const [rowsPath, setRowsPath] = useState(endpoint?.rows_path ?? "");
  const [paging, setPaging] = useState<Paging>(endpoint?.pagination ?? "none");
  const [pageParam, setPageParam] = useState(endpoint?.page_param ?? "");
  const [nextPath, setNextPath] = useState(endpoint?.next_path ?? "");
  const [addressError, setAddressError] = useState<string | null>(null);
  const [sample, setSample] = useState<EndpointTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>(
    endpoint?.field_mapping ?? {},
  );
  const [mappingError, setMappingError] = useState<string | null>(null);

  useEffect(() => {
    setUrl(endpoint?.url ?? "");
    setFormat(endpoint?.format ?? "json");
    setRowsPath(endpoint?.rows_path ?? "");
    setPaging(endpoint?.pagination ?? "none");
    setPageParam(endpoint?.page_param ?? "");
    setNextPath(endpoint?.next_path ?? "");
    setMapping(endpoint?.field_mapping ?? {});
  }, [endpoint]);

  // A CSV answer is one file: no rows path and no paging (the server forces it).
  const isCsv = format === "csv";
  const address = {
    url: url.trim(),
    format,
    rows_path: isCsv ? "" : rowsPath.trim(),
    pagination: isCsv ? ("none" as const) : paging,
    page_param: !isCsv && paging === "page_param" ? pageParam.trim() : "",
    next_path: !isCsv && paging === "next_link" ? nextPath.trim() : "",
  };
  const addressChanged =
    !endpoint ||
    (Object.keys(address) as (keyof typeof address)[]).some((k) => address[k] !== endpoint[k]);
  const pagingIncomplete =
    (address.pagination === "page_param" && !address.page_param) ||
    (address.pagination === "next_link" && !address.next_path);

  // Columns from the last test, plus any the saved mapping names, so a saved
  // mapping still shows before the first test of this visit.
  const columns = useMemo(() => {
    const cols = [...(sample?.columns ?? [])];
    for (const c of Object.values(endpoint?.field_mapping ?? {})) {
      if (c && !cols.includes(c)) cols.push(c);
    }
    return cols;
  }, [sample, endpoint]);

  const missing = fields.filter((f) => f.required && !mapping[f.key]);
  const mappingDirty =
    JSON.stringify(clean(mapping)) !== JSON.stringify(clean(endpoint?.field_mapping ?? {}));

  const saveAddress = async () => {
    setAddressError(null);
    try {
      if (endpoint) {
        await update.mutateAsync({ id: endpoint.id, ...address });
        toast({ title: "Endpoint saved" });
      } else {
        await add.mutateAsync({ kind, ...address });
        toast({ title: "Endpoint added", description: "Now run a test to see its columns." });
      }
      setSample(null);
    } catch (err) {
      const env = extractErrorEnvelope(err);
      setAddressError(Object.values(env.errors ?? {})[0]?.[0] ?? env.detail);
    }
  };

  const runTest = async () => {
    if (!endpoint) return;
    setTestError(null);
    try {
      setSample(await test.mutateAsync(endpoint.id));
    } catch (err) {
      setSample(null);
      setTestError(extractErrorEnvelope(err).detail);
    }
  };

  const saveMapping = async () => {
    if (!endpoint) return;
    setMappingError(null);
    try {
      await update.mutateAsync({ id: endpoint.id, field_mapping: clean(mapping) });
      toast({ title: "Mapping saved" });
    } catch (err) {
      const env = extractErrorEnvelope(err);
      setMappingError(env.errors?.field_mapping?.[0] ?? env.detail);
    }
  };

  const onDelete = async () => {
    if (!endpoint) return;
    const ok = await confirm({
      title: `Remove the ${copy.what} endpoint?`,
      description: `Its address and column mapping are deleted. ${copy.title} already synced stay.`,
      confirmLabel: "Remove",
      destructive: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(endpoint.id);
      setSample(null);
    } catch (err) {
      toast({
        title: "Could not remove",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{copy.title} endpoint</CardTitle>
        <CardDescription>
          The address that returns your {copy.what} as JSON or CSV. Test it, then
          match its columns to Ordo's fields. {copy.note}
        </CardDescription>
        {endpoint && (
          <CardAction>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onDelete}
              aria-label={`Remove the ${copy.what} endpoint`}
            >
              <Trash2 strokeWidth={2} />
            </Button>
          </CardAction>
        )}
      </CardHeader>

      <CardContent className="space-y-8">
        <section aria-labelledby={`${kind}-address`} className="space-y-4">
          <h3 id={`${kind}-address`} className={STEP_LABEL}>
            <span className={STEP_NUMBER}>1</span> Address
          </h3>
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-[1fr_160px]">
            <div className="space-y-1.5">
              <Label htmlFor={`${kind}-url`}>URL</Label>
              <Input
                id={`${kind}-url`}
                type="url"
                inputMode="url"
                placeholder={`https://portal.your-school.edu/api/${kind}`}
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`${kind}-format`}>Format</Label>
              <Select value={format} onValueChange={(v) => setFormat(v as Format)}>
                <SelectTrigger id={`${kind}-format`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="json">JSON</SelectItem>
                  <SelectItem value="csv">CSV</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          {isCsv ? (
            <p className="text-[13px] text-muted-foreground">
              Ordo reads the whole file in one call. Its first line must name the
              columns; save it as UTF-8, separated by commas.
            </p>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
                <div className="space-y-1.5">
                  <Label htmlFor={`${kind}-rows-path`}>Rows at</Label>
                  <Input
                    id={`${kind}-rows-path`}
                    placeholder={copy.rowsPath}
                    value={rowsPath}
                    onChange={(e) => setRowsPath(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`${kind}-paging`}>Pages</Label>
                  <Select value={paging} onValueChange={(v) => setPaging(v as Paging)}>
                    <SelectTrigger id={`${kind}-paging`} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {(Object.keys(PAGING_LABELS) as Paging[]).map((p) => (
                        <SelectItem key={p} value={p}>
                          {PAGING_LABELS[p]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {paging === "page_param" && (
                  <div className="space-y-1.5">
                    <Label htmlFor={`${kind}-page-param`}>Page parameter</Label>
                    <Input
                      id={`${kind}-page-param`}
                      placeholder="page"
                      value={pageParam}
                      onChange={(e) => setPageParam(e.target.value)}
                    />
                  </div>
                )}
                {paging === "next_link" && (
                  <div className="space-y-1.5">
                    <Label htmlFor={`${kind}-next-path`}>Next link at</Label>
                    <Input
                      id={`${kind}-next-path`}
                      placeholder="meta.next"
                      value={nextPath}
                      onChange={(e) => setNextPath(e.target.value)}
                    />
                  </div>
                )}
              </div>
              <p className="text-[13px] text-muted-foreground">
                "Rows at" is the dotted path to the list of {copy.what} in the answer;
                leave it blank when the answer is the list itself.{" "}
                {paging === "page_param"
                  ? "Ordo asks for page 1, 2, 3 and on until a page comes back empty."
                  : paging === "next_link"
                    ? "Ordo follows the link at this path in each answer until it is empty. It must stay on the same host."
                    : "Ordo reads one answer only."}
              </p>
            </>
          )}
          {addressError && (
            <Alert variant="destructive">
              <AlertDescription>{addressError}</AlertDescription>
            </Alert>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              variant={endpoint ? "outline" : "default"}
              onClick={saveAddress}
              disabled={
                !url.trim() || pagingIncomplete || !addressChanged || add.isPending || update.isPending
              }
            >
              <Link2 data-icon="inline-start" strokeWidth={2} />
              {endpoint ? "Save address" : "Add endpoint"}
            </Button>
            {endpoint && (
              <Button
                variant="outline"
                onClick={runTest}
                disabled={test.isPending || addressChanged}
              >
                <FlaskConical data-icon="inline-start" strokeWidth={2} />
                {test.isPending ? "Testing…" : "Test"}
              </Button>
            )}
          </div>
        </section>

        {endpoint && (
          <section aria-labelledby={`${kind}-sample`} className="space-y-4">
            <h3 id={`${kind}-sample`} className={STEP_LABEL}>
              <span className={STEP_NUMBER}>2</span> What your portal sends
            </h3>
            {testError ? (
              <Alert variant="destructive">
                <AlertTitle>The test call failed</AlertTitle>
                <AlertDescription>{testError}</AlertDescription>
              </Alert>
            ) : sample ? (
              <SamplePreview sample={sample} what={copy.what} />
            ) : (
              <p className="rounded-[12px] border border-dashed border-[color:var(--border)] px-5 py-6 text-[13.5px] text-muted-foreground">
                Press <strong className="font-semibold text-foreground">Test</strong> to fetch the
                first page. Ordo shows up to 20 rows here and stores none of them.
              </p>
            )}
          </section>
        )}

        {endpoint && (
          <section aria-labelledby={`${kind}-mapping`} className="space-y-4">
            <h3 id={`${kind}-mapping`} className={STEP_LABEL}>
              <span className={STEP_NUMBER}>3</span> Match columns to Ordo's fields
            </h3>
            {columns.length === 0 ? (
              <p className="text-[13.5px] text-muted-foreground">
                Run a test first. Its columns appear here to choose from.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[200px]">Ordo field</TableHead>
                      <TableHead className="w-[220px]">Your column</TableHead>
                      <TableHead>Sample values</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {fields.map((f) => {
                      const column = mapping[f.key] ?? "";
                      return (
                        <TableRow key={f.key}>
                          <TableCell className="font-medium">
                            {f.label}
                            {f.required && (
                              <span className="ml-1 text-destructive" aria-label="required">
                                *
                              </span>
                            )}
                          </TableCell>
                          <TableCell>
                            <Select
                              value={column || NONE}
                              onValueChange={(v) =>
                                setMapping((m) => ({ ...m, [f.key]: v === NONE ? "" : String(v) }))
                              }
                            >
                              <SelectTrigger aria-label={`Column for ${f.label}`}>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value={NONE}>Not mapped</SelectItem>
                                {columns.map((c) => (
                                  <SelectItem key={c} value={c}>
                                    {c}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </TableCell>
                          <TableCell className="max-w-[320px] truncate text-muted-foreground">
                            {column && sample
                              ? sample.sample_rows
                                  .slice(0, SAMPLE_VALUES)
                                  .map((r) => r[column] || "—")
                                  .join(" · ")
                              : ""}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
            {mappingError && (
              <Alert variant="destructive">
                <AlertDescription>{mappingError}</AlertDescription>
              </Alert>
            )}
            {columns.length > 0 && missing.length > 0 && (
              <p className="text-[13px] text-muted-foreground">
                Still to map: {missing.map((f) => f.label).join(", ")}.
              </p>
            )}
          </section>
        )}
      </CardContent>

      {endpoint && columns.length > 0 && (
        <CardFooter className="justify-end">
          <Button
            onClick={saveMapping}
            disabled={missing.length > 0 || !mappingDirty || update.isPending}
          >
            <Check data-icon="inline-start" strokeWidth={2} />
            {update.isPending ? "Saving…" : "Save mapping"}
          </Button>
        </CardFooter>
      )}
    </Card>
  );
}

const STEP_LABEL =
  "flex items-center gap-2.5 font-mono text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground";
const STEP_NUMBER =
  "grid size-5 place-items-center rounded-full bg-[color:var(--accent-iris)] text-[10px] text-[color:var(--accent-iris-fg)]";

function clean(mapping: Record<string, string>) {
  return Object.fromEntries(
    Object.entries(mapping)
      .filter(([, v]) => v)
      .sort(([a], [b]) => a.localeCompare(b)),
  );
}

function SamplePreview({ sample, what }: { sample: EndpointTestResult; what: string }) {
  const rows = sample.sample_rows.slice(0, PREVIEW_ROWS);
  return (
    <div className="space-y-3">
      <p className="text-[13.5px]">
        <span className="font-semibold tabular-nums">{sample.row_count_on_page}</span>{" "}
        {sample.row_count_on_page === 1 ? "row" : "rows"} on the first page,{" "}
        <span className="font-semibold tabular-nums">{sample.columns.length}</span>{" "}
        {sample.columns.length === 1 ? "column" : "columns"}.
        {sample.sample_rows.length > PREVIEW_ROWS &&
          ` Showing the first ${PREVIEW_ROWS}.`}
      </p>
      {rows.length > 0 ? (
        <div className="overflow-x-auto rounded-[12px] border border-[color:var(--border)]">
          <Table>
            <TableHeader>
              <TableRow>
                {sample.columns.map((c) => (
                  <TableHead key={c} className="whitespace-nowrap font-mono text-[11px]">
                    {c}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r, i) => (
                <TableRow key={i}>
                  {sample.columns.map((c) => (
                    <TableCell key={c} className="max-w-[220px] truncate whitespace-nowrap">
                      {r[c] ?? ""}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <Alert>
          <AlertDescription>
            The first page came back empty. Check the address and "Rows at", or
            make sure the portal has {what} to send.
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
