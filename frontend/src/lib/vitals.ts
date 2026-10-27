import { onCLS, onINP, onLCP, type Metric } from "web-vitals";
import { apiUrl } from "@/lib/api";

/**
 * Reports Web Vitals (LCP, INP, CLS) to Ordo once per page view (spec 0006,
 * AC-26), by `sendBeacon` so a report still goes out as the tab closes.
 * The page is the matched route pattern (`/students/:id`), never the URL,
 * so no ids or query strings leave the browser.
 */

let installed = false;
let page = "";

/** A TanStack Router full path as a route pattern: `/students/$id/` → `/students/:id`. */
export function routePattern(fullPath: string): string {
  const pattern = fullPath
    .replace(/\$(\w+)/g, ":$1")
    .replace(/\/\$$/, "/*")
    .replace(/(.)\/$/, "$1");
  return pattern || "/";
}

/** The route the next report belongs to. */
export function setVitalsPage(fullPath: string) {
  page = fullPath ? routePattern(fullPath) : "";
}

function send(metric: Metric) {
  try {
    if (!page) return;
    const body = JSON.stringify({ name: metric.name, value: metric.value, page });
    const url = apiUrl("/api/telemetry/vitals/");
    // A string body goes as text/plain, so no CORS preflight is needed.
    if (navigator.sendBeacon?.(url, body)) return;
    void fetch(url, { method: "POST", body, keepalive: true }).catch(() => {
      // A lost vital is fine.
    });
  } catch {
    // Reporting must never break the page.
  }
}

/** Start reporting. Safe to call more than once. */
export function installVitals() {
  if (installed || typeof window === "undefined") return;
  installed = true;
  onLCP(send);
  onINP(send);
  onCLS(send);
}
