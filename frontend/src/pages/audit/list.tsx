import { useState } from "react";
import { Download } from "lucide-react";
import { useAuditLogs, type AuditLogParams } from "@/api/audit";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { extractErrorEnvelope } from "@/lib/api";
import { downloadAuthenticatedFile } from "@/lib/download";
import { toast } from "@/lib/use-toast";
import { orEmpty } from "@/lib/format";

const STATUS_ALL = "all";

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function AuditLogListPage() {
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<typeof STATUS_ALL | "success" | "failed">(
    STATUS_ALL,
  );
  const [exporting, setExporting] = useState(false);

  const params: AuditLogParams = {
    page,
    query: query || undefined,
    status: status === STATUS_ALL ? undefined : status,
  };
  const list = useAuditLogs(params);

  const onExport = async () => {
    setExporting(true);
    try {
      const exportParams: Record<string, string> = {};
      if (query) exportParams.query = query;
      if (status !== STATUS_ALL) exportParams.status = status;
      await downloadAuthenticatedFile(
        "/audit-logs/export/",
        "audit-log.csv",
        exportParams,
      );
    } catch (err) {
      toast({
        title: "Export failed",
        description: extractErrorEnvelope(err).detail,
        variant: "destructive",
      });
    } finally {
      setExporting(false);
    }
  };

  return (
    <ListShell
      section="Admin · Audit log"
      title="Audit log"
      description="Every sign-in and change made in Ordo, with who did it, when and from where."
      toolbar={
        <Button variant="outline" onClick={onExport} disabled={exporting}>
          <Download data-icon="inline-start" />
          {exporting ? "Exporting…" : "Export CSV"}
        </Button>
      }
      filters={
        <Select
          value={status}
          onValueChange={(v) => {
            setStatus(v as typeof STATUS_ALL | "success" | "failed");
            setPage(1);
          }}
        >
          <SelectTrigger className="h-9 w-[160px] font-mono text-[12px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={STATUS_ALL}>All outcomes</SelectItem>
            <SelectItem value="success">Successful</SelectItem>
            <SelectItem value="failed">Failed</SelectItem>
          </SelectContent>
        </Select>
      }
      query={query}
      onQueryChange={(q) => {
        setQuery(q);
        setPage(1);
      }}
      searchPlaceholder="Search action, email, or path"
      isLoading={list.isLoading}
      error={list.error}
      isEmpty={!list.data?.results.length}
      emptyMessage="No activity recorded yet."
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
            <TableHead className="w-[180px]">When</TableHead>
            <TableHead>User</TableHead>
            <TableHead>Action</TableHead>
            <TableHead className="w-[90px]">Status</TableHead>
            <TableHead className="w-[140px]">IP</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {list.data?.results.map((log) => (
            <TableRow key={log.id}>
              <TableCell className="whitespace-nowrap font-mono text-[12px] text-muted-foreground">
                {formatTimestamp(log.created_at)}
              </TableCell>
              <TableCell>
                <span className="block font-medium">{log.user_name}</span>
                {log.user_email && log.user_email !== log.user_name && (
                  <span className="block text-[12px] text-muted-foreground">
                    {log.user_email}
                  </span>
                )}
              </TableCell>
              <TableCell>
                <span className="block">{log.action}</span>
                <span className="block font-mono text-[11px] text-muted-foreground">
                  {log.method} {log.path}
                </span>
              </TableCell>
              <TableCell>
                <Badge variant={log.succeeded ? "secondary" : "destructive"}>
                  {orEmpty(log.status_code)}
                </Badge>
              </TableCell>
              <TableCell className="font-mono text-[12px] text-muted-foreground">
                {orEmpty(log.ip_address)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </ListShell>
  );
}
