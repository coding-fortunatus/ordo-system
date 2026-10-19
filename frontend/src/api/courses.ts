import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { PaginatedResponse, PortalOwned } from "./types";

export type ExamType = "PBE" | "CBE";

export interface Course extends PortalOwned {
  id: number;
  name: string;
  code: string;
  exam_type: ExamType;
}

export interface CourseListParams {
  page?: number;
  query?: string;
  exam_type?: ExamType;
  department?: string;
}

export interface CourseInput {
  name: string;
  code: string;
  exam_type: ExamType;
}

const KEY = ["courses"] as const;

export function useCourses(params: CourseListParams = {}) {
  return useQuery({
    queryKey: [...KEY, params],
    queryFn: async () => {
      const res = await api.get<PaginatedResponse<Course>>("/courses/", {
        params,
      });
      return res.data;
    },
    placeholderData: keepPreviousData,
  });
}

export function useCreateCourse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: CourseInput) => {
      const res = await api.post<Course>("/courses/", data);
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useUpdateCourse(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: CourseInput) => {
      const res = await api.patch<Course>(`/courses/${id}/`, data);
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeleteCourse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/courses/${id}/`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
