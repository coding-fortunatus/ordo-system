import { useState } from "react";
import { Plus, Search } from "lucide-react";
import {
  type Faculty,
  useDeleteFaculty,
  useFaculties,
} from "@/api/faculties";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useConfirm } from "@/lib/confirm";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import { PaginationFooter } from "@/components/data-table/pagination";
import { PageHeader } from "@/components/layout/page-header";
import { ACTIONS_HEAD_CLASS, RowActions } from "@/components/data-table/row-actions";
import { EMPTY } from "@/lib/format";
import { FromPortalBadge } from "@/components/from-portal-badge";
import { FacultyFormDialog } from "./faculty-form-dialog";

export default function FacultiesListPage() {
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Faculty | null>(null);

  const list = useFaculties({ page, query: query || undefined });
  const remove = useDeleteFaculty();
  const confirm = useConfirm();

  const handleDelete = async (fac: Faculty) => {
    const ok = await confirm({
      title: "Delete faculty?",
      description: `"${fac.name}" will be permanently removed.`,
      confirmLabel: "Delete",
      destructive: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(fac.slug);
      toast({ title: "Faculty deleted" });
    } catch (err) {
      const envelope = extractErrorEnvelope(err);
      toast({
        title: "Delete failed",
        description: envelope.detail,
        variant: "destructive",
      });
    }
  };

  return (
    <div className="space-y-10">
      <PageHeader
        section="Admin · Faculties"
        title="Faculties"
        description="Group your departments into faculties. A faculty can hold as many departments as you need."
        actions={
          <Button
            onClick={() => {
              setEditing(null);
              setDialogOpen(true);
            }}
          >
            <Plus data-icon="inline-start" strokeWidth={2.25} />
            New faculty
          </Button>
        }
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
          All faculties
        </span>
        <div className="relative w-full sm:max-w-xs">
          <Search
            className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            strokeWidth={2}
          />
          <Input
            placeholder="Search by name or code"
            className="h-9 pl-9 font-mono text-[12px]"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(1);
            }}
          />
        </div>
      </div>

      <Card>
        <CardContent className="pt-6">
          {list.error && (
            <Alert variant="destructive" className="mb-4">
              <AlertDescription>
                {extractErrorEnvelope(list.error).detail}
              </AlertDescription>
            </Alert>
          )}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[120px]">Code</TableHead>
                <TableHead>Faculty</TableHead>
                <TableHead>Departments</TableHead>
                <TableHead className="w-[120px] text-right">Count</TableHead>
                <TableHead className={ACTIONS_HEAD_CLASS}>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.isLoading ? (
                Array.from({ length: 6 }).map((_, i) => (
                  <TableRow key={`s-${i}`}>
                    <TableCell colSpan={5}>
                      <Skeleton className="h-6 w-full" />
                    </TableCell>
                  </TableRow>
                ))
              ) : list.data?.results.length ? (
                list.data.results.map((f) => (
                  <TableRow key={f.id}>
                    <TableCell>
                      <kbd className="rounded-[4px] border border-[color:var(--border)] bg-[color:var(--muted)] px-2 py-0.5 font-mono text-[11px] tracking-wide">
                        {f.slug}
                      </kbd>
                    </TableCell>
                    <TableCell className="font-serif text-[1.0625rem] tracking-[-0.005em]">
                      <div className="flex items-center gap-2">
                        {f.name}
                        <FromPortalBadge record={f} />
                      </div>
                    </TableCell>
                    <TableCell>
                      {f.departments.length === 0 ? (
                        <span className="font-serif italic text-muted-foreground">
                          {EMPTY}
                        </span>
                      ) : (
                        <div className="flex flex-wrap gap-1.5">
                          {f.departments.map((d) => (
                            <span
                              key={d.id}
                              className="rounded-[4px] border border-[color:var(--border)] bg-[color:var(--muted)] px-2 py-0.5 font-mono text-[11px] tracking-wide"
                              title={d.name}
                            >
                              {d.slug}
                            </span>
                          ))}
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {f.department_count.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right">
                      <RowActions>
                        <Button
                          size="xs"
                          variant="outline"
                          onClick={() => {
                            setEditing(f);
                            setDialogOpen(true);
                          }}
                        >
                          Edit
                        </Button>
                        {!f.from_portal && (
                          <Button
                            size="xs"
                            variant="destructive"
                            onClick={() => handleDelete(f)}
                            disabled={remove.isPending}
                          >
                            Delete
                          </Button>
                        )}
                      </RowActions>
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell
                    colSpan={5}
                    className="py-12 text-center font-serif italic text-muted-foreground"
                  >
                    No faculties found.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
          {list.data && (
            <PaginationFooter
              page={page}
              pageSize={15}
              total={list.data.count}
              onPageChange={setPage}
            />
          )}
        </CardContent>
      </Card>

      <FacultyFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initial={editing}
      />
    </div>
  );
}
