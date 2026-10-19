import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { PortalOwned } from "./types";

export interface Department extends PortalOwned {
  id: number;
  name: string;
  slug: string;
  class_count: number;
  student_count: number;
  faculty: number | null;
  faculty_name: string | null;
  faculty_slug: string | null;
}

export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface DepartmentListParams {
  page?: number;
  page_size?: number;
  query?: string;
  all?: boolean;
  enabled?: boolean;
}

const KEY = ["departments"] as const;

export function useDepartments(params: DepartmentListParams = {}) {
  const { enabled = true, ...query } = params;
  return useQuery({
    queryKey: [...KEY, query],
    queryFn: async () => {
      const res = await api.get<PaginatedResponse<Department> | Department[]>(
        "/departments/",
        { params: query },
      );
      // When ?all=true is used the backend returns a plain array instead of
      // the paginated envelope; normalize so callers can always read .results.
      const data = res.data;
      if (Array.isArray(data)) {
        return {
          count: data.length,
          next: null,
          previous: null,
          results: data,
        } satisfies PaginatedResponse<Department>;
      }
      return data;
    },
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useDepartment(slug: string | undefined) {
  return useQuery({
    queryKey: [...KEY, "detail", slug],
    queryFn: async () => {
      const res = await api.get<Department>(`/departments/${slug}/`);
      return res.data;
    },
    enabled: !!slug,
  });
}

export interface DepartmentInput {
  name: string;
  slug: string;
  faculty?: number | null;
}

export function useCreateDepartment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: DepartmentInput) => {
      const res = await api.post<Department>("/departments/", data);
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}

export function useUpdateDepartment(originalSlug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: DepartmentInput) => {
      const res = await api.patch<Department>(
        `/departments/${originalSlug}/`,
        data,
      );
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}

export function useDeleteDepartment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (slug: string) => {
      await api.delete(`/departments/${slug}/`);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}
