import { useEffect, useRef, useState } from "react";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api, getToken, wsUrl } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { PaginatedResponse } from "./types";

export type JobType = "timetable" | "distribution" | "allocation" | "portal_sync";
export type JobStatus = "pending" | "running" | "success" | "failed";

export interface BackgroundJob {
  job_id: string;
  job_type: JobType;
  job_type_display: string;
  status: JobStatus;
  progress: number;
  started_at: string;
  completed_at: string | null;
  error_message: string;
  result_data: Record<string, unknown>;
  params: Record<string, unknown>;
  created_by_email: string;
}

export interface JobProgressEvent {
  job_id: string;
  job_type: JobType;
  status: JobStatus;
  progress: number;
  error_message: string;
  result: Record<string, unknown> | null;
}

/** Something a finished run left undone (courses not scheduled, students
 * without a seat), from ``result_data.warnings``. */
export interface JobWarning {
  title: string;
  items: string[];
  hint: string;
}

export function jobWarnings(
  result: Record<string, unknown> | null | undefined,
): JobWarning[] {
  const w = result?.warnings;
  return Array.isArray(w) ? (w as JobWarning[]) : [];
}

const KEY = ["jobs"] as const;

export function useJobs(params: { status?: JobStatus; job_type?: JobType; page?: number } = {}) {
  return useQuery({
    queryKey: [...KEY, params],
    queryFn: async () => {
      const res = await api.get<PaginatedResponse<BackgroundJob>>("/jobs/", {
        params,
      });
      return res.data;
    },
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
  });
}

export function useJob(jobId: string | undefined) {
  return useQuery({
    queryKey: [...KEY, "detail", jobId],
    queryFn: async () => {
      const res = await api.get<BackgroundJob>(`/jobs/${jobId}/`);
      return res.data;
    },
    enabled: !!jobId,
    refetchInterval: (q) => {
      const data = q.state.data;
      if (!data) return 2000;
      return data.status === "running" || data.status === "pending"
        ? 2000
        : false;
    },
  });
}

export function useDeleteJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string) => {
      await api.delete(`/jobs/${jobId}/`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useRetryJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string) => {
      const res = await api.post<{ job_id: string }>(`/jobs/${jobId}/retry/`);
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useGenerateTimetable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: { start_date: string; end_date: string }) => {
      const res = await api.post<{ job_id: string }>(
        "/timetable/generate/",
        data,
      );
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useGenerateDistribution() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: { date: string; period: "AM" | "PM" }) => {
      const res = await api.post<{ job_id: string }>(
        "/distribution/generate/",
        data,
      );
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useGenerateAllocation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: { date: string; period: "AM" | "PM" }) => {
      const res = await api.post<{ job_id: string }>(
        "/allocation/generate/",
        data,
      );
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useGenerateDistributionAll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const res = await api.post<{ job_id: string }>(
        "/distribution/generate-all/",
        {},
      );
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useGenerateAllocationAll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const res = await api.post<{ job_id: string }>(
        "/allocation/generate-all/",
        {},
      );
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

/** Subscribe to real-time job progress over WebSocket. Falls back to REST
 *  polling if the socket can't be opened. Returns ``null`` until the first
 *  event arrives. */
export function useJobProgress(jobId: string | null) {
  const auth = useAuth();
  const [event, setEvent] = useState<JobProgressEvent | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fallbackQc = useQueryClient();

  useEffect(() => {
    if (!jobId || auth.status !== "authenticated") return;
    const token = getToken();
    if (!token) return;

    const url = wsUrl(`/ws/jobs/${jobId}/?token=${token}`);
    let cancelled = false;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data) as JobProgressEvent;
          setEvent(data);
          if (data.status === "success" || data.status === "failed") {
            fallbackQc.invalidateQueries({ queryKey: ["jobs"] });
          }
        } catch {
          // Ignore malformed frames.
        }
      };
      ws.onerror = () => {
        // Trigger fallback REST poll.
        if (!cancelled) startPollingFallback();
      };
    } catch {
      startPollingFallback();
    }

    let pollInterval: ReturnType<typeof setInterval> | null = null;
    function startPollingFallback() {
      if (pollInterval) return;
      pollInterval = setInterval(async () => {
        try {
          const res = await api.get<BackgroundJob>(`/jobs/${jobId}/`);
          if (cancelled) return;
          setEvent({
            job_id: res.data.job_id,
            job_type: res.data.job_type,
            status: res.data.status,
            progress: res.data.progress,
            error_message: res.data.error_message,
            result:
              res.data.status === "success" ? res.data.result_data : null,
          });
          if (res.data.status === "success" || res.data.status === "failed") {
            if (pollInterval) clearInterval(pollInterval);
          }
        } catch {
          // ignore transient errors
        }
      }, 2000);
    }

    return () => {
      cancelled = true;
      if (pollInterval) clearInterval(pollInterval);
      const ws = wsRef.current;
      if (ws && ws.readyState <= WebSocket.OPEN) {
        ws.close();
      }
      wsRef.current = null;
    };
  }, [jobId, auth.status, fallbackQc]);

  return event;
}
