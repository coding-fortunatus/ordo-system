import type { ReactNode } from "react";
import { Search } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { extractErrorEnvelope } from "@/lib/api";

interface Props {
  /** The section chip, "<sidebar group> · <page>" (or "Console · <group>"). */
  section: string;
  title: string;
  description?: string;
  toolbar?: ReactNode;
  query?: string;
  onQueryChange?: (q: string) => void;
  searchPlaceholder?: string;
  isLoading: boolean;
  error: unknown;
  isEmpty: boolean;
  emptyMessage?: string;
  children: ReactNode;
  pagination?: ReactNode;
  filters?: ReactNode;
}

export function ListShell({
  section,
  title,
  description,
  toolbar,
  query,
  onQueryChange,
  searchPlaceholder = "Search",
  isLoading,
  error,
  isEmpty,
  emptyMessage = "No records found.",
  children,
  pagination,
  filters,
}: Props) {
  return (
    <div className="space-y-10">
      <PageHeader section={section} title={title} description={description} actions={toolbar} />

      {/* Toolbar row */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
            All {title.toLowerCase()}
          </span>
          {filters}
        </div>
        {onQueryChange && (
          <div className="relative w-full sm:max-w-xs">
            <Search
              className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              strokeWidth={2}
            />
            <Input
              placeholder={searchPlaceholder}
              className="h-9 pl-9 font-mono text-[12px]"
              value={query ?? ""}
              onChange={(e) => onQueryChange(e.target.value)}
            />
          </div>
        )}
      </div>

      {/* Body card */}
      <div className="overflow-hidden rounded-none border border-[color:var(--border)] bg-card">
        {error ? (
          <Alert variant="destructive" className="m-4">
            <AlertDescription>
              {extractErrorEnvelope(error).detail}
            </AlertDescription>
          </Alert>
        ) : null}
        {isLoading ? (
          <div className="space-y-2 p-6">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : isEmpty ? (
          <div className="grid place-items-center px-6 py-20 text-center">
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
              No records
            </p>
            <p className="mt-3 font-serif text-[1.25rem] italic text-muted-foreground">
              {emptyMessage}
            </p>
          </div>
        ) : (
          children
        )}
        {pagination && (
          <div className="border-t border-[color:var(--border)] bg-[color:var(--muted)]/40 px-4 py-3">
            {pagination}
          </div>
        )}
      </div>
    </div>
  );
}
