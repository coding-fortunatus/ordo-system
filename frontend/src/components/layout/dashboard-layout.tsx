import { useEffect, useState } from "react";
import { Link, Outlet } from "@tanstack/react-router";
import type { AppPath } from "@/lib/paths";
import {
  Armchair,
  Boxes,
  Building2,
  CalendarDays,
  ChevronsLeft,
  ChevronsRight,
  ClipboardList,
  Download,
  FileText,
  GraduationCap,
  Grid3x3,
  Landmark,
  LayoutDashboard,
  ListChecks,
  type LucideIcon,
  LogOut,
  Menu,
  Moon,
  RefreshCw,
  School,
  ScrollText,
  Settings,
  SlidersHorizontal,
  Sun,
  Upload,
  Users,
  X,
} from "lucide-react";
import {
  canManageData,
  canManageFaculties,
  isCommittee,
  isSuperAdmin,
  ROLE_LABELS,
  useAuth,
  type AuthUser,
} from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme-toggle";
import { NotificationBell } from "@/components/notification-bell";
import { SupportBanner } from "@/components/support-banner";
import { AnnouncementBanner } from "@/components/announcement-banner";
import { useNotificationStream } from "@/api/notifications";
import { type FeatureKey, useSchoolPlan } from "@/api/school";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Logo, Wordmark } from "@/components/logo";
import { InstitutionLogo } from "@/components/institution-logo";
import { formatEmail, formatName, isBlank } from "@/lib/format";

interface NavSection {
  label: string;
  /** Token suffix on var(--accent-{tone}). Drives section dot + active stripe color. */
  tone: "iris" | "amber" | "coral" | "teal" | "lime" | "plum";
  items: NavItem[];
}

interface NavItem {
  to: AppPath;
  label: string;
  icon: LucideIcon;
  /** When set, the item is shown only if the predicate passes for the user. */
  requires?: (u: AuthUser | null) => boolean;
  /** When set, the item is shown only if the school's plan includes it. */
  feature?: FeatureKey;
}

const NAV_SECTIONS: NavSection[] = [
  {
    label: "Overview",
    tone: "iris",
    items: [{ to: "/", label: "Dashboard", icon: LayoutDashboard }],
  },
  {
    label: "Catalog",
    tone: "amber",
    items: [
      {
        to: "/departments",
        label: "Departments",
        icon: Building2,
        requires: canManageData,
      },
      { to: "/classes", label: "Classes", icon: School },
      { to: "/courses", label: "Courses", icon: ClipboardList },
      { to: "/students", label: "Students", icon: GraduationCap },
      { to: "/halls", label: "Halls", icon: Grid3x3, requires: canManageData },
    ],
  },
  {
    label: "Operations",
    tone: "coral",
    items: [
      { to: "/timetable", label: "Timetable", icon: CalendarDays },
      {
        to: "/distribution",
        label: "Distribution",
        icon: Boxes,
        requires: isCommittee,
      },
      {
        to: "/allocation",
        label: "Allocation",
        icon: Armchair,
        requires: isCommittee,
        feature: "seat_allocation",
      },
      {
        to: "/directory",
        label: "Directory & VISA",
        icon: FileText,
        requires: isCommittee,
      },
      { to: "/exports", label: "Exports", icon: Download },
    ],
  },
  {
    label: "Admin",
    tone: "teal",
    items: [
      {
        to: "/faculties",
        label: "Faculties",
        icon: Landmark,
        requires: canManageFaculties,
      },
      { to: "/uploads", label: "Uploads", icon: Upload, requires: canManageData },
      {
        to: "/portal-sync",
        label: "Portal sync",
        icon: RefreshCw,
        requires: canManageData,
        feature: "portal_sync",
      },
      { to: "/users", label: "Users", icon: Users, requires: isSuperAdmin },
      { to: "/jobs", label: "Jobs", icon: ListChecks, requires: isSuperAdmin },
      {
        to: "/audit",
        label: "Audit log",
        icon: ScrollText,
        requires: isSuperAdmin,
      },
      {
        to: "/constraints",
        label: "Constraints",
        icon: SlidersHorizontal,
        requires: isSuperAdmin,
      },
      { to: "/settings", label: "Settings", icon: Settings, requires: isSuperAdmin },
    ],
  },
];

const COLLAPSED_KEY = "ordo:sidebar-collapsed";

/** Brand mark as a home link: no underline, subtle hover, visible focus. */
const BRAND_LINK =
  "inline-flex items-center outline-none transition-opacity hover:opacity-80 focus-visible:ring-2 focus-visible:ring-ring/40";

function SidebarContent({
  sections,
  collapsed,
  onToggle,
  onNavigate,
}: {
  sections: NavSection[];
  collapsed: boolean;
  onToggle?: () => void;
  onNavigate?: () => void;
}) {
  const { user, logout } = useAuth();
  const { theme, toggle: toggleTheme } = useTheme();
  const isDark = theme === "dark";
  const initials = (() => {
    const first = user?.first_name?.[0] ?? "";
    const last = user?.last_name?.[0] ?? "";
    return (first + last).toUpperCase() || user?.email?.[0]?.toUpperCase() || "?";
  })();
  const displayName = isBlank(user?.full_name)
    ? formatEmail(user?.email)
    : formatName(user?.full_name);

  return (
    <>
      {/* Brand */}
      <div
        className={cn(
          "flex h-16 shrink-0 items-center border-b border-[color:var(--sidebar-border)]",
          collapsed ? "justify-center px-3" : "gap-2.5 px-6",
        )}
      >
        {collapsed ? (
          <Link to="/" aria-label="Go to dashboard" className={BRAND_LINK}>
            <Logo size={22} />
          </Link>
        ) : (
          <>
            <Link to="/" aria-label="Go to dashboard" className={BRAND_LINK}>
              <Wordmark />
            </Link>
            <InstitutionLogo size={32} className="ml-auto" />
          </>
        )}
      </div>

      {/* Nav */}
      <nav
        className={cn(
          "flex-1 space-y-7 overflow-y-auto py-7",
          collapsed ? "px-2" : "px-4",
        )}
      >
        {sections.map((section) => {
          const tone = section.tone;
          const accentBg = `var(--accent-${tone})`;
          const accentFg = `var(--accent-${tone}-fg)`;
          return (
            <div key={section.label} className="space-y-1.5">
              {!collapsed ? (
                <div className="flex items-center gap-2 px-2">
                  <span
                    aria-hidden
                    className="size-1.5 rounded-full"
                    style={{ backgroundColor: accentFg }}
                  />
                  <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                    {section.label}
                  </p>
                </div>
              ) : (
                <div
                  aria-hidden
                  className="mx-auto h-px w-6 bg-[color:var(--sidebar-border)]"
                />
              )}
              <div className="space-y-0.5">
                {section.items.map(({ to, label, icon: Icon }) => (
                  <Link
                    key={to}
                    to={to}
                    activeOptions={{ exact: to === "/" }}
                    onClick={onNavigate}
                    title={collapsed ? label : undefined}
                    style={
                      {
                        "--nav-bg": accentBg,
                        "--nav-fg": accentFg,
                      } as React.CSSProperties
                    }
                    className={cn(
                      "group relative flex items-center rounded-md text-[13.5px] transition-all duration-200",
                      collapsed ? "h-9 justify-center" : "gap-2.5 px-2.5 py-1.5",
                    )}
                    activeProps={{
                      className:
                        "bg-[color:var(--nav-bg)] text-[color:var(--nav-fg)] shadow-[inset_0_0_0_1px_color-mix(in_oklch,var(--nav-fg)_22%,transparent)]",
                    }}
                    inactiveProps={{
                      className:
                        "text-muted-foreground hover:bg-[color:var(--nav-bg)]/60 hover:text-[color:var(--nav-fg)]",
                    }}
                  >
                    {({ isActive }) => (
                      <>
                        {!collapsed && (
                          <span
                            aria-hidden
                            className="h-4 w-px shrink-0 rounded-full transition-all"
                            style={{
                              backgroundColor: isActive
                                ? accentFg
                                : "transparent",
                            }}
                          />
                        )}
                        {collapsed && isActive && (
                          <span
                            aria-hidden
                            className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-r"
                            style={{ backgroundColor: accentFg }}
                          />
                        )}
                        <Icon
                          className="size-3.5 shrink-0"
                          strokeWidth={isActive ? 2.25 : 2}
                        />
                        {!collapsed && (
                          <span className="font-medium">{label}</span>
                        )}
                      </>
                    )}
                  </Link>
                ))}
              </div>
            </div>
          );
        })}
      </nav>

      {/* Notifications */}
      <div
        className={cn(
          "shrink-0 border-t border-[color:var(--sidebar-border)] py-2",
          collapsed ? "flex justify-center px-2" : "px-4",
        )}
      >
        <NotificationBell label={!collapsed} side="right" />
      </div>

      {/* Collapse toggle */}
      {onToggle && (
        <div
          className={cn(
            "shrink-0 border-t border-[color:var(--sidebar-border)] py-2",
            collapsed ? "px-2" : "px-4",
          )}
        >
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={onToggle}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className={cn(
              "w-full text-muted-foreground",
              collapsed ? "justify-center" : "justify-start",
            )}
          >
            {collapsed ? (
              <ChevronsRight strokeWidth={2} />
            ) : (
              <>
                <ChevronsLeft data-icon="inline-start" strokeWidth={2} />
                Collapse
              </>
            )}
          </Button>
        </div>
      )}

      {/* User */}
      <div
        className={cn(
          "shrink-0 border-t border-[color:var(--sidebar-border)]",
          collapsed ? "p-2" : "p-3",
        )}
      >
        <DropdownMenu>
          <DropdownMenuTrigger
            className={cn(
              "flex w-full items-center rounded-md text-left transition-colors hover:bg-[color:var(--brand-soft)]/40 focus:outline-none focus:ring-1 focus:ring-[color:var(--brand)]/40",
              collapsed ? "justify-center p-1.5" : "gap-3 p-2",
            )}
            title={collapsed ? displayName : "Account menu"}
          >
            <span
              className="grid size-8 shrink-0 place-items-center rounded-full font-mono text-[11px] font-medium"
              style={{
                backgroundColor: "var(--brand)",
                color: "var(--brand-foreground)",
                boxShadow:
                  "0 4px 14px -6px color-mix(in oklch, var(--brand) 60%, transparent)",
              }}
            >
              {initials}
            </span>
            {!collapsed && (
              <span className="flex-1 overflow-hidden">
                <span className="block truncate text-[13px] font-medium text-foreground">
                  {displayName}
                </span>
                <span className="block truncate font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                  {user?.role
                    ? ROLE_LABELS[user.role]
                    : user?.department?.slug ?? "Department Officer"}
                </span>
              </span>
            )}
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="start"
            side="top"
            sideOffset={8}
            className="min-w-[200px]"
          >
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
            <DropdownMenuItem
              onClick={() => {
                void logout();
              }}
            >
              <LogOut className="mr-2 size-3.5" strokeWidth={2} />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </>
  );
}

export function DashboardLayout() {
  const { user, logout } = useAuth();
  useNotificationStream();
  const plan = useSchoolPlan();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(COLLAPSED_KEY) === "1";
  });

  useEffect(() => {
    window.localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  const sections = NAV_SECTIONS.map((s) => ({
    ...s,
    items: s.items.filter(
      (i) =>
        (!i.requires || i.requires(user)) &&
        (!i.feature || !plan.data || plan.data.features.includes(i.feature)),
    ),
  })).filter((s) => s.items.length > 0);

  return (
    <div className="relative flex h-screen overflow-hidden bg-background">
      {/* Desktop sidebar — fixed height, internal scroll only */}
      <aside
        className={cn(
          "hidden h-screen shrink-0 flex-col border-r border-[color:var(--sidebar-border)] bg-[color:var(--sidebar)] transition-[width] duration-200 ease-out lg:flex",
          collapsed ? "w-[68px]" : "w-64",
        )}
      >
        <SidebarContent
          sections={sections}
          collapsed={collapsed}
          onToggle={() => setCollapsed((v) => !v)}
        />
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 flex lg:hidden"
          role="dialog"
          aria-modal
        >
          <button
            type="button"
            aria-label="Close menu"
            onClick={() => setMobileOpen(false)}
            className="absolute inset-0 bg-foreground/30 backdrop-blur-sm"
          />
          <aside className="relative flex h-full w-72 flex-col border-r border-[color:var(--sidebar-border)] bg-[color:var(--sidebar)]">
            <Button
              type="button"
              variant="outline"
              size="icon-sm"
              aria-label="Close menu"
              onClick={() => setMobileOpen(false)}
              className="absolute right-3 top-3.5 bg-background"
            >
              <X strokeWidth={2} />
            </Button>
            <SidebarContent
              sections={sections}
              collapsed={false}
              onNavigate={() => setMobileOpen(false)}
            />
          </aside>
        </div>
      )}

      {/* Right column: only this area scrolls */}
      <div className="flex h-screen flex-1 flex-col overflow-hidden">
        <SupportBanner />
        <AnnouncementBanner />
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-[color:var(--border)] bg-background/80 px-5 backdrop-blur-md lg:hidden">
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
          >
            <Menu strokeWidth={2} />
          </Button>
          <Link to="/" aria-label="Go to dashboard" className={BRAND_LINK}>
            <Wordmark size="sm" />
          </Link>
          <div className="flex items-center gap-2">
            <NotificationBell />
            <ThemeToggle size="sm" iconOnly />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                void logout();
              }}
            >
              <LogOut data-icon="inline-start" strokeWidth={2} />
              Sign out
            </Button>
          </div>
        </header>
        <main className="flex-1 overflow-y-auto px-6 py-10 lg:px-12 lg:py-14">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
