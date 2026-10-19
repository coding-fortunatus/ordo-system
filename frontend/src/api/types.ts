export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/**
 * Normalize a list response into the paginated envelope.
 *
 * Endpoints called with `?all=true` skip pagination and return a plain array,
 * so callers would otherwise have to branch on the shape. Wrapping it here
 * means `.results` always holds the full set — which is what pickers need,
 * since the default page size of 15 silently truncates them.
 */
export function toPaginated<T>(data: PaginatedResponse<T> | T[]): PaginatedResponse<T> {
  if (Array.isArray(data)) {
    return { count: data.length, next: null, previous: null, results: data };
  }
  return data;
}

/** Set on records the school portal syncs (spec 0005, AC-8). */
export interface PortalOwned {
  /** Synced from the school's portal: it can't be deleted here. */
  from_portal: boolean;
  /** API fields only the portal may change, e.g. `name`, `class_id`. */
  portal_locked: string[];
}

export interface DepartmentRef {
  id: number;
  name: string;
  slug: string;
}

export interface ClassRef {
  id: number;
  name: string | null;
}
