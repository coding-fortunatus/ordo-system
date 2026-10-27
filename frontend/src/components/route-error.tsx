import { useEffect } from "react";
import { type ErrorComponentProps, useRouter } from "@tanstack/react-router";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { errorReference } from "@/lib/api";
import { reportError } from "@/lib/error-reporter";

/**
 * Shown in place of a page that crashed while loading or rendering. The
 * error goes to Ordo's error tracker, and the rest of the app (sidebar,
 * session) stays as it was.
 */
export function RouteError({ error, reset }: ErrorComponentProps) {
  const router = useRouter();
  // Only when the server answered with an error: there's a trace to find.
  const reference = errorReference(error);

  useEffect(() => {
    reportError(error);
  }, [error]);

  return (
    <div role="alert" className="flex min-h-[60vh] items-center justify-center px-6 py-16">
      <div className="max-w-md text-center">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          Something went wrong
        </p>
        <h1 className="mt-4 font-serif text-[2.25rem] leading-[1.05] tracking-[-0.02em] text-balance">
          This page didn't load
        </h1>
        <p className="mt-4 text-[15px] leading-[1.7] text-muted-foreground">
          The error has been sent to the Ordo team. Try again, and if it keeps happening, let us
          know what you were doing.
        </p>
        {reference && (
          <p className="mt-4 text-[13px] text-muted-foreground">
            Reference: <span className="select-all font-mono text-foreground">{reference}</span>
          </p>
        )}
        <div className="mt-8 flex flex-wrap justify-center gap-2">
          <Button
            variant="outline"
            onClick={() => {
              reset();
              void router.invalidate();
            }}
          >
            <RotateCcw data-icon="inline-start" />
            Try again
          </Button>
          <Button variant="ghost" onClick={() => window.location.assign("/")}>
            Go to the start
          </Button>
        </div>
      </div>
    </div>
  );
}
