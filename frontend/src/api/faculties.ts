import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api";
import { toPaginated, type PaginatedResponse, type PortalOwned } from "./types";

export interface FacultyDepartmentBrief {
  id: number;
  name: string;
  slug: string;
}

export interface Faculty extends PortalOwned {
  id: number;
  name: string;
  slug: string;
  department_count: number;
  departments: FacultyDepartmentBrief[];
}

export interface FacultyListParams {
  page?: number;
  query?: string;
  enabled?: boolean;
  /** Fetch every faculty in one response, bypassing pagination. For pickers. */
  all?: boolean;
}

const KEY = ["faculties"] as const;

export function useFaculties(params: FacultyListParams = {}) {
  const { enabled = true, ...query } = params;
  return useQuery({
    queryKey: [...KEY, query],
    queryFn: async () => {
      const res = await api.get<PaginatedResponse<Faculty> | Faculty[]>(
        "/faculties/",
        { params: query },
      );
      return toPaginated(res.data);
    },
    placeholderData: keepPreviousData,
    enabled,
  });
}

export interface FacultyInput {
  name: string;
  slug: string;
  department_ids?: number[];
}

export function useCreateFaculty() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: FacultyInput) => {
      const res = await api.post<Faculty>("/faculties/", data);
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: ["departments"] });
    },
  });
}

export function useUpdateFaculty(originalSlug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: FacultyInput) => {
      const res = await api.patch<Faculty>(
        `/faculties/${originalSlug}/`,
        data,
      );
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: ["departments"] });
    },
  });
}

export function useDeleteFaculty() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (slug: string) => {
      await api.delete(`/faculties/${slug}/`);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: ["departments"] });
    },
  });
}
