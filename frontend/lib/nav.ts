import {
  LayoutDashboard,
  BarChart3,
  Wallet,
  Receipt,
  type LucideIcon,
} from "lucide-react";

/**
 * Single source of truth for primary navigation routes.
 *
 * The four nav surfaces historically maintained their own route lists and they
 * DISAGREE on purpose (per-surface membership is part of the locked UX):
 *   - desktop AppHeader primary nav: Track, Compare, Holdings, Activity
 *   - mobile BottomTabBar tabbed nav: Track, Compare, Activity (NO Holdings —
 *     Holdings lives behind the "More" sheet on mobile)
 *   - mobile MoreSheet overflow: Holdings, Settings
 *
 * The `surfaces` field encodes exactly that membership so each component can
 * derive its own list from this one table WITHOUT changing what renders.
 * BottomTabBar additionally needs the `key` + `icon` it always used.
 *
 * NOTE: MobileAppBar is intentionally NOT modeled here. It resolves a *page
 * title* from a 10-entry longest-prefix list (including sub-routes like
 * /holdings/active and /holdings/i/) — a different concern from nav-destination
 * membership, and folding it in would not be behavior-preserving.
 */

export type NavSurface = "header" | "tabbar" | "more";

export interface NavRoute {
  href: string;
  label: string;
  /** BottomTabBar's stable React key (only the tabbar routes carry one). */
  key?: "track" | "compare" | "activity";
  /** BottomTabBar icon (only the tabbar routes carry one). */
  icon?: LucideIcon;
  surfaces: readonly NavSurface[];
}

export const NAV_ROUTES: readonly NavRoute[] = [
  { href: "/track", label: "Track", key: "track", icon: LayoutDashboard, surfaces: ["header", "tabbar"] },
  { href: "/compare", label: "Compare", key: "compare", icon: BarChart3, surfaces: ["header", "tabbar"] },
  // Holdings: desktop header + mobile More sheet, but NOT the mobile tab bar.
  { href: "/holdings", label: "Holdings", icon: Wallet, surfaces: ["header", "more"] },
  { href: "/activity", label: "Activity", key: "activity", icon: Receipt, surfaces: ["header", "tabbar"] },
  // Instruments: mobile More sheet only (desktop reaches it via the UserMenu).
  { href: "/instruments", label: "Instruments", surfaces: ["more"] },
  // Settings: mobile More sheet only (desktop reaches it via the UserMenu).
  { href: "/settings", label: "Settings", surfaces: ["more"] },
];

export function navRoutesFor(surface: NavSurface): readonly NavRoute[] {
  return NAV_ROUTES.filter((r) => r.surfaces.includes(surface));
}

/**
 * Active-route test shared by AppHeader and BottomTabBar (identical logic):
 * exact match OR a descendant path (`href + "/"` prefix).
 */
export function isRouteActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(href + "/");
}
