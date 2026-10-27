import { useEffect, useState } from "react";
import {
  HeadContent,
  Outlet,
  Scripts,
  createRootRoute,
  useRouterState,
} from "@tanstack/react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { getConfig } from "@/lib/server-config";
import { setApiOrigin } from "@/lib/api";
import { makeQueryClient } from "@/lib/query";
import { AuthProvider } from "@/lib/auth";
import { ConfirmProvider } from "@/lib/confirm";
import { ThemeProvider } from "@/lib/theme";
import { BrandApplier, brandCss } from "@/lib/brand";
import { Toaster } from "@/components/toaster";
import { installErrorReporter } from "@/lib/error-reporter";
import { installVitals, setVitalsPage } from "@/lib/vitals";
import { TooltipProvider } from "@/components/ui/tooltip";
import styles from "@/index.css?url";

// Apply the theme before paint to avoid a flash of the wrong palette. Runs
// before any module loads, so it inlines lib/theme-cookie.ts — keep in sync.
const THEME_SCRIPT = `(function(){try{var m=document.cookie.match(/(?:^|;\\s*)ordo-theme=(light|dark)/);var s=m?m[1]:localStorage.getItem("ordo:theme");var t=s==="light"||s==="dark"?s:matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";var r=document.documentElement;if(t==="dark")r.classList.add("dark");r.style.colorScheme=t}catch(_){}})()`;

// Bump when the icons change: Chrome caches favicons per page URL and will
// otherwise keep showing (or swap back to) the old one.
const ICON_VERSION = "3";

export const Route = createRootRoute({
  // Origins + branding, resolved on the server for the requesting host.
  loader: () => getConfig(),
  staleTime: Infinity,
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1.0" },
      { name: "color-scheme", content: "light dark" },
      { title: "Ordo | Exam scheduling, hall distribution & seat allocation" },
      {
        name: "description",
        content:
          "Ordo schedules, distributes, seats, and documents every exam. Automated timetable generation, hall distribution, seat allocation, and broadsheet exports.",
      },
    ],
    links: [
      { rel: "stylesheet", href: styles },
      { rel: "icon", type: "image/svg+xml", href: `/favicon.svg?v=${ICON_VERSION}` },
      { rel: "icon", type: "image/png", sizes: "64x64", href: `/favicon-32.png?v=${ICON_VERSION}` },
      { rel: "apple-touch-icon", href: `/apple-touch-icon.png?v=${ICON_VERSION}` },
    ],
  }),
  component: RootComponent,
});

function RootComponent() {
  const { origins, branding, host } = Route.useLoaderData();
  // Set during render, so it's in place before any page's queries run.
  setApiOrigin(origins.api);
  useEffect(() => installErrorReporter(host), [host]);
  // Web Vitals from the school dashboard and the console only (spec 0006).
  const page = useRouterState({ select: (state) => state.matches[state.matches.length - 1]?.fullPath ?? "" });
  const reportsVitals = host === "school" || host === "console";
  useEffect(() => setVitalsPage(reportsVitals ? page : ""), [page, reportsVitals]);
  useEffect(() => {
    if (reportsVitals) installVitals();
  }, [reportsVitals]);
  const [queryClient] = useState(makeQueryClient);
  const brand = brandCss(branding?.brand_color);

  return (
    // The theme script sets `class`/`style` before hydration.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
        <HeadContent />
        {brand && <style dangerouslySetInnerHTML={{ __html: brand }} />}
      </head>
      <body>
        <ThemeProvider>
          <QueryClientProvider client={queryClient}>
            <BrandApplier />
            <AuthProvider>
              <ConfirmProvider>
                <TooltipProvider delay={150}>
                  <Outlet />
                </TooltipProvider>
                <Toaster />
              </ConfirmProvider>
            </AuthProvider>
          </QueryClientProvider>
        </ThemeProvider>
        <Scripts />
      </body>
    </html>
  );
}
