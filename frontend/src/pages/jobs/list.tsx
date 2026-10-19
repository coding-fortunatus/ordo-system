import { useState } from "react";
import {
  type BackgroundJob,
  type JobStatus,
  type JobType,
  useDeleteJob,
  useJobs,
  useRetryJob,
  jobWarnings,
} from "@/api/jobs";
import { Button } from "@/components/ui/button";
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
import { ListShell } from "@/components/data-table/list-shell";
import { PaginationFooter } from "@/components/data-table/pagination";
import { useAuth } from "@/lib/auth";
import { useConfirm } from "@/lib/confirm";
import { extractErrorEnvelope } from "@/lib/api";
import { toast } from "@/lib/use-toast";
import { JobProgressDialog } from "@/components/job-progress-dialog";
import { ACTIONS_HEAD_CLASS, RowActions } from "@/components/data-table/row-actions";

export default function JobsListPage() {
  const { user } = useAuth();
  const isAdmin = !!user?.is_staff;

  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string>("__all__");
  const [typeFilter, setTypeFilter] = useState<string>("__all__");
  const [activeJob, setActiveJob] = useState<BackgroundJob | null>(null);
  const [open, setOpen] = useState(false);

  const list = useJobs({
    page,
    status:
      statusFilter === "__all__" ? undefined : (statusFilter as JobStatus),
    job_type:
      typeFilter === "__all__" ? undefined : (typeFilter as JobType),
  });
  const remove = useDeleteJob();
  const retry = useRetryJob();
  const confirm = useConfirm();

  const onDelete = async (job: BackgroundJob) => {
    const ok = await confirm({
      title: "Delete job?",
      description: `This ${job.job_type_display.toLowerCase()} job will be removed from history.`,
      confirmLabel: "Delete",
      destructive: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(job.job_id);
      toast({ title: "Job deleted" });
    } catch (err) {
      toast({
        title: "Delete failed",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  const onRetry = async (job: BackgroundJob) => {
    try {
      const out = await retry.mutateAsync(job.job_id);
      toast({ title: "Retry queued", description: out.job_id });
    } catch (err) {
      toast({
        title: "Retry failed",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    }
  };

  return (
    <>
      <ListShell
        title="Jobs"
        description="Long-running tasks like timetable generation, and how they went. Admins can see everyone's."
        filters={
          <>
            <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v ?? "__all__"); setPage(1); }}>
              <SelectTrigger>
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All statuses</SelectItem>
                <SelectItem value="pending">Pending</SelectItem>
                <SelectItem value="running">Running</SelectItem>
                <SelectItem value="success">Success</SelectItem>
                <SelectItem value="failed">Failed</SelectItem>
              </SelectContent>
            </Select>
            <Select value={typeFilter} onValueChange={(v) => { setTypeFilter(v ?? "__all__"); setPage(1); }}>
              <SelectTrigger>
                <SelectValue placeholder="Type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All types</SelectItem>
                <SelectItem value="timetable">Timetable</SelectItem>
                <SelectItem value="distribution">Distribution</SelectItem>
                <SelectItem value="allocation">Allocation</SelectItem>
                <SelectItem value="portal_sync">Portal sync</SelectItem>
              </SelectContent>
            </Select>
          </>
        }
        isLoading={list.isLoading}
        error={list.error}
        isEmpty={!list.data?.results.length}
        emptyMessage="No background jobs yet."
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
              <TableHead className="w-[40px]" />
              <TableHead>Type</TableHead>
              <TableHead className="w-[140px]">Status</TableHead>
              <TableHead className="w-[180px]">Progress</TableHead>
              <TableHead className="w-[180px]">Started</TableHead>
              <TableHead>By</TableHead>
              <TableHead className={ACTIONS_HEAD_CLASS}>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.results.map((job) => {
              const warned =
                job.status === "success" && jobWarnings(job.result_data).length > 0;
              const tone = statusTone(warned ? "warnings" : job.status);
              return (
                <TableRow key={job.job_id}>
                  <TableCell>
                    <span
                      className="grid size-6 place-items-center rounded-full"
                      style={{ backgroundColor: tone.bg }}
                    >
                      {job.status === "running" ? (
                        <span className="relative inline-flex size-1.5">
                          <span
                            className="absolute inset-0 animate-ping rounded-full"
                            style={{ backgroundColor: tone.fg, opacity: 0.6 }}
                          />
                          <span
                            className="relative size-1.5 rounded-full"
                            style={{ backgroundColor: tone.fg }}
                          />
                        </span>
                      ) : (
                        <span
                          className="size-1.5 rounded-full"
                          style={{ backgroundColor: tone.fg }}
                        />
                      )}
                    </span>
                  </TableCell>
                  <TableCell>
                    <p className="font-serif text-[1rem] tracking-[-0.005em]">
                      {job.job_type_display}
                    </p>
                    <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                      {job.job_id.slice(0, 12)}…
                    </p>
                  </TableCell>
                  <TableCell>
                    <span
                      className="rounded-full px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em]"
                      style={{ backgroundColor: tone.bg, color: tone.fg }}
                    >
                      {warned ? "warnings" : job.status}
                    </span>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span className="relative h-1 w-24 overflow-hidden rounded-full bg-[color:var(--border)]">
                        <span
                          className="absolute inset-y-0 left-0 transition-[width]"
                          style={{
                            width: `${job.progress}%`,
                            backgroundColor: tone.fg,
                          }}
                        />
                      </span>
                      <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
                        {job.progress}%
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <span className="font-mono text-[12px] tabular-nums text-muted-foreground">
                      {new Date(job.started_at).toLocaleString("en-GB", {
                        dateStyle: "short",
                        timeStyle: "short",
                      })}
                    </span>
                  </TableCell>
                  <TableCell>
                    <span className="font-mono text-[12px] text-muted-foreground">
                      {job.created_by_email}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    <RowActions>
                      <Button
                        size="xs"
                        variant="outline"
                        onClick={() => {
                          setActiveJob(job);
                          setOpen(true);
                        }}
                      >
                        View
                      </Button>
                      {isAdmin && job.status === "failed" && (
                        <Button
                          size="xs"
                          variant="outline"
                          onClick={() => onRetry(job)}
                          disabled={retry.isPending}
                        >
                          Retry
                        </Button>
                      )}
                      {isAdmin && (
                        <Button
                          size="xs"
                          variant="destructive"
                          onClick={() => onDelete(job)}
                          disabled={
                            remove.isPending || job.status === "running"
                          }
                        >
                          Delete
                        </Button>
                      )}
                    </RowActions>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </ListShell>
      <JobProgressDialog
        jobId={activeJob?.job_id ?? null}
        title={activeJob ? activeJob.job_type_display : "Job"}
        open={open}
        onOpenChange={setOpen}
      />
    </>
  );
}

function statusTone(s: string) {
  if (s === "success")
    return { bg: "var(--accent-green)", fg: "var(--accent-green-fg)" };
  if (s === "failed")
    return { bg: "var(--accent-red)", fg: "var(--accent-red-fg)" };
  if (s === "running")
    return { bg: "var(--accent-blue)", fg: "var(--accent-blue-fg)" };
  return { bg: "var(--accent-yellow)", fg: "var(--accent-yellow-fg)" };
}
