"use client";

import { usePathname } from "next/navigation";
import { HoldingsTabs } from "./_components/HoldingsTabs";

export default function HoldingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  // Instrument detail (/holdings/i/[id]) is a structural descendant of /holdings under the
  // App Router, so this layout WOULD wrap it by default — layouts apply to every nested
  // segment, dynamic routes included. There is no built-in "skip this segment" prop; the
  // canonical opt-out is a client-component guard reading usePathname() and pass-through
  // returning bare {children}. We want NO H1 and NO tab strip on
  // instrument-detail, so we early-return here. The instrument-detail page keeps its own
  // <main>; the layout contributes nothing in that case. See HoldingsTabs.tsx for the
  // defense-in-depth mirror guard.
  if (pathname.startsWith("/holdings/i/")) {
    return <>{children}</>;
  }

  return (
    <main className="mx-auto max-w-7xl px-4 py-6 md:px-6 md:py-8">
      <h1 className="mb-2 text-2xl font-semibold leading-tight">Holdings</h1>
      <HoldingsTabs />
      <div className="mt-4">{children}</div>
    </main>
  );
}
