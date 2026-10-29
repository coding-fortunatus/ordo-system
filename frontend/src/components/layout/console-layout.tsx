import { Link, Outlet, useLocation, useNavigate } from "@tanstack/react-router";
import {
  Activity,
  BellRing,
  BookOpen,
  ChevronsUpDown,
  Gauge,
  Layers,
  LayoutDashboard,
  LayoutGrid,
  LineChart,
  LogOut,
  type LucideIcon,
  Megaphone,
  Moon,
  School,
  Logs,
  ScrollText,
  Search,
  ShieldCheck,
  Waypoints,
  Sun,
  UserRound,
} from "lucide-react";
import type { AppPath } from "@/lib/paths";
import {
  isPlatformOwner,
  PLATFORM_ROLE_LABELS,
  usePlatformAuth,
  type PlatformAdmin,
} from "@/lib/platform-auth";
import { useTheme } from "@/lib/theme";
import { Logo } from "@/components/logo";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Separator } from "@/components/ui/separator";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";

interface NavItem {
  to: AppPath;
  label: string;
  icon: LucideIcon;
  requires?: (a: PlatformAdmin | null) => boolean;
}

const NAV_GROUPS: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "Platform",
    items: [
      { to: "/console", label: "Dashboard", icon: LayoutDashboard },
      { to: "/console/schools", label: "Schools", icon: School },
      { to: "/console/health", label: "Health", icon: Activity },
      { to: "/console/staff", label: "Find staff", icon: Search },
    ],
  },
  {
    // Metrics, logs and traces (spec 0006); more pages join as they ship.
    label: "Observability",
    items: [
      { to: "/console/observability", label: "Overview", icon: Gauge },
      { to: "/console/observability/traces", label: "Traces", icon: Waypoints },
      { to: "/console/observability/logs", label: "Logs", icon: Logs },
      { to: "/console/observability/metrics", label: "Metrics", icon: LineChart },
      { to: "/console/observability/dashboards", label: "Dashboards", icon: LayoutGrid },
      { to: "/console/observability/alerts", label: "Alerts", icon: BellRing },
      { to: "/console/observability/guide", label: "Guide", icon: BookOpen },
    ],
  },
  {
    label: "Manage",
    items: [
      { to: "/console/plans", label: "Plans", icon: Layers },
      { to: "/console/announcements", label: "Announcements", icon: Megaphone },
      { to: "/console/admins", label: "Admins", icon: ShieldCheck, requires: isPlatformOwner },
      { to: "/console/audit", label: "Audit log", icon: ScrollText },
    ],
  },
];

const NAV = NAV_GROUPS.flatMap((g) => g.items);

/** Pages outside the nav that still name the header. */
const EXTRA_TITLES: Array<[string, string]> = [["/console/account", "Your account"]];

function isActive(pathname: string, { to }: NavItem) {
  // The dashboard is the console root, so only an exact match counts.
  if (to === "/console") return pathname === "/console" || pathname === "/console/";
  return pathname.startsWith(to);
}

function ConsoleSidebar() {
  const { admin, logout } = usePlatformAuth();
  const { theme, toggle: toggleTheme } = useTheme();
  const { pathname } = useLocation();
  const { isMobile, setOpenMobile } = useSidebar();
  const navigate = useNavigate();
  const groups = NAV_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((item) => !item.requires || item.requires(admin)),
  }));
  const isDark = theme === "dark";
  const initials =
    admin?.name
      .split(/\s+/)
      .map((part) => part[0])
      .join("")
      .slice(0, 2)
      .toUpperCase() || "?";

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              size="lg"
              tooltip="Dashboard"
              render={<Link to="/console" aria-label="Dashboard" />}
            >
              <Logo size={22} />
              <span className="flex flex-col gap-0.5 leading-none">
                <span className="font-heading text-[15px] font-semibold tracking-[-0.03em]">Ordo</span>
                <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                  Console
                </span>
              </span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        {groups.map((group) => (
        <SidebarGroup key={group.label}>
          <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {group.items.map((item) => (
                <SidebarMenuItem key={item.to}>
                  <SidebarMenuButton
                    isActive={isActive(pathname, item)}
                    tooltip={item.label}
                    render={<Link to={item.to} onClick={() => isMobile && setOpenMobile(false)} />}
                  >
                    <item.icon strokeWidth={2} />
                    <span>{item.label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        ))}
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <SidebarMenuButton size="lg" aria-label="Account menu" className="data-popup-open:bg-sidebar-accent" />
                }
              >
                <span
                  className="grid size-8 shrink-0 place-items-center rounded-full font-mono text-[11px] font-medium"
                  style={{ backgroundColor: "var(--brand)", color: "var(--brand-foreground)" }}
                >
                  {initials}
                </span>
                <span className="grid flex-1 text-left leading-tight">
                  <span className="truncate text-[13px] font-medium">{admin?.name}</span>
                  <span className="truncate font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                    {admin ? PLATFORM_ROLE_LABELS[admin.role] : ""}
                  </span>
                </span>
                <ChevronsUpDown className="ml-auto" />
              </DropdownMenuTrigger>
              <DropdownMenuContent
                side={isMobile ? "bottom" : "right"}
                align="end"
                sideOffset={8}
                className="min-w-[220px]"
              >
                <DropdownMenuGroup>
                  <DropdownMenuLabel>
                    <span className="block truncate text-[13px] font-medium">{admin?.name}</span>
                    <span className="block truncate text-[12px] font-normal text-muted-foreground">
                      {admin?.email}
                    </span>
                  </DropdownMenuLabel>
                </DropdownMenuGroup>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={() => {
                    if (isMobile) setOpenMobile(false);
                    void navigate({ to: "/console/account" });
                  }}
                >
                  <UserRound className="mr-2 size-3.5" strokeWidth={2} />
                  Your account
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={(e) => {
                    e.preventDefault();
                    toggleTheme();
                  }}
                >
                  {isDark ? (
                    <Sun className="mr-2 size-3.5" strokeWidth={2} />
                  ) : (
                    <Moon className="mr-2 size-3.5" strokeWidth={2} />
                  )}
                  {isDark ? "Light mode" : "Dark mode"}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => void logout()}>
                  <LogOut className="mr-2 size-3.5" strokeWidth={2} />
                  Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}

/** The signed in console at console.<base>: sidebar nav and the page. */
export function ConsoleLayout() {
  const { pathname } = useLocation();
  const current =
    NAV.find((item) => isActive(pathname, item))?.label ??
    EXTRA_TITLES.find(([path]) => pathname.startsWith(path))?.[1];

  return (
    <SidebarProvider>
      <ConsoleSidebar />
      <SidebarInset>
        <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-2 border-b border-[color:var(--border)] bg-background/90 px-4 backdrop-blur">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-1 h-4" />
          <span className="text-[13.5px] font-medium">{current ?? "Console"}</span>
        </header>
        <div className="mx-auto w-full max-w-screen-2xl px-4 py-10 sm:px-6 lg:px-10">
          <Outlet />
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}
