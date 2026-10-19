import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Course } from "./courses";
import { toPaginated, type DepartmentRef, type PaginatedResponse, type PortalOwned } from "./types";

export type ExamPeriod = "" | "AM" | "PM";

export interface Class extends PortalOwned {
  id: number;
  name: string | null;
  /** Display copy of the student count, kept in step by the server. Read-only. */
  size: number;
  department: DepartmentRef;
  courses: Course[];
  student_count: number;
  /** Off = not sitting this session: left out of every planning stage. */
  is_active: boolean;
  /** Override for the VISA short code; blank => auto-derived. */
  visa_code: string;
  /** Resolved VISA code (override if set, else auto-derived). Read-only. */
  visa_label: string;
}

export interface ClassListParams {
  page?: number;
  query?: string;
  department?: string;
  /** Fetch every class in one response, bypassing pagination. For pickers. */
  all?: boolean;
}

export interface ClassInput {
  name: string;
  department_id: number;
  visa_code?: string;
  is_active?: boolean;
}

const KEY = ["classes"] as const;

export function useClasses(params: ClassListParams = {}) {
  return useQuery({
    queryKey: [...KEY, params],
    queryFn: async () => {
      const res = await api.get<PaginatedResponse<Class> | Class[]>(
        "/classes/",
        { params },
      );
      return toPaginated(res.data);
    },
    placeholderData: keepPreviousData,
  });
}

export function useClass(id: number | undefined) {
  return useQuery({
    queryKey: [...KEY, "detail", id],
    queryFn: async () => {
      const res = await api.get<Class>(`/classes/${id}/`);
      return res.data;
    },
    enabled: id !== undefined,
  });
}

export function useCreateClass() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: ClassInput) => {
      const res = await api.post<Class>("/classes/", data);
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useUpdateClass(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: Partial<ClassInput>) => {
      const res = await api.patch<Class>(`/classes/${id}/`, data);
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

/** Switch a class on or off for this session. Refreshes the readiness
 * checks too, since switching a class off can unblock a generate run. */
export function useSetClassActive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, is_active }: { id: number; is_active: boolean }) => {
      const res = await api.patch<Class>(`/classes/${id}/`, { is_active });
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: ["readiness"] });
    },
  });
}

export function useDeleteClass() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/classes/${id}/`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export interface ClassCourseAssignInput {
  course_id?: number;
  name?: string;
  code?: string;
  exam_type?: "PBE" | "CBE";
}

export function useAddCourseToClass(classId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: ClassCourseAssignInput) => {
      const res = await api.post<Course>(`/classes/${classId}/courses/`, data);
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useRemoveCourseFromClass(classId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (courseId: number) => {
      await api.delete(`/classes/${classId}/courses/${courseId}/`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
