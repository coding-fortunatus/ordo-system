import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api";
import { SCHOOL_PLAN_KEY } from "./school";
import type { ClassRef, DepartmentRef, PaginatedResponse, PortalOwned } from "./types";

export interface Student extends PortalOwned {
  id: number;
  first_name: string;
  last_name: string;
  matric_no: string;
  email: string;
  phone: string;
  department: DepartmentRef | null;
  level: ClassRef | null;
}

export interface StudentInput {
  first_name: string;
  last_name: string;
  matric_no: string;
  email: string;
  phone: string;
  class_id: number;
}

export interface StudentListParams {
  page?: number;
  query?: string;
  department?: string;
  class?: number;
}

const KEY = ["students"] as const;

export function useStudents(params: StudentListParams = {}) {
  return useQuery({
    queryKey: [...KEY, params],
    queryFn: async () => {
      const res = await api.get<PaginatedResponse<Student>>("/students/", {
        params,
      });
      return res.data;
    },
    placeholderData: keepPreviousData,
  });
}

export function useCreateStudent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: StudentInput) => {
      const res = await api.post<Student>("/students/", data);
      return res.data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY });
      // Plan usage counts these.
      void qc.invalidateQueries({ queryKey: SCHOOL_PLAN_KEY });
    },
  });
}

export function useUpdateStudent(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: Partial<StudentInput>) => {
      const res = await api.patch<Student>(`/students/${id}/`, data);
      return res.data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY });
      // Plan usage counts these.
      void qc.invalidateQueries({ queryKey: SCHOOL_PLAN_KEY });
    },
  });
}

export function useDeleteStudent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/students/${id}/`);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY });
      // Plan usage counts these.
      void qc.invalidateQueries({ queryKey: SCHOOL_PLAN_KEY });
    },
  });
}
