import { Badge } from "@/components/ui/badge";
import { FormDescription } from "@/components/ui/form";
import type { PortalOwned } from "@/api/types";

/** Marks a record synced from the school's portal (spec 0005). The fields
 * the portal owns are read only here; the school changes them in its portal
 * and the next sync brings them in. */
export function FromPortalBadge({ record }: { record: PortalOwned }) {
  if (!record.from_portal) return null;
  return (
    <Badge
      variant="secondary"
      title="Synced from your school portal. Change it there, then sync."
    >
      From portal
    </Badge>
  );
}

/** True when the portal owns `field` on `record`, so its input is read only. */
export function portalLocked(record: PortalOwned | null | undefined, field: string) {
  return Boolean(record?.portal_locked?.includes(field));
}

/** The note under a read only form field the portal owns. Use inside a
 * `FormItem`. */
export function PortalLockedHint({ locked }: { locked: boolean }) {
  if (!locked) return null;
  return (
    <FormDescription className="text-[12px]">
      From your school portal. Change it there, then sync.
    </FormDescription>
  );
}
