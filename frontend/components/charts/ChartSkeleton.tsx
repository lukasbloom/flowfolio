import { cn } from "@/lib/utils";

type ChartSkeletonVariant = "line" | "bar" | "donut";

/**
 * Chart-shaped loading placeholder. Faint gridlines/axis plus a ghosted
 * line / bar / donut silhouette, pulsing. Size it with `className` to match the
 * real chart's footprint (same height classes) so the swap causes no layout shift.
 */
export function ChartSkeleton({
  variant,
  className,
}: {
  variant: ChartSkeletonVariant;
  className?: string;
}) {
  return (
    <div
      role="status"
      aria-label="Loading chart"
      className={cn("animate-pulse text-muted-foreground", className)}
    >
      {variant === "donut" ? (
        <DonutSkeleton />
      ) : variant === "bar" ? (
        <BarSkeleton />
      ) : (
        <LineSkeleton />
      )}
    </div>
  );
}

const GRIDLINES = [12, 24, 36, 48];

function LineSkeleton() {
  return (
    <svg
      aria-hidden="true"
      className="h-full w-full"
      viewBox="0 0 100 60"
      preserveAspectRatio="none"
    >
      {GRIDLINES.map((y) => (
        <line
          key={y}
          x1="0"
          x2="100"
          y1={y}
          y2={y}
          stroke="currentColor"
          strokeOpacity="0.15"
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
        />
      ))}
      <polyline
        points="0,46 14,32 28,38 42,20 56,28 70,14 84,24 100,10"
        fill="none"
        stroke="currentColor"
        strokeOpacity="0.4"
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      <line
        x1="0"
        x2="100"
        y1="54"
        y2="54"
        stroke="currentColor"
        strokeOpacity="0.25"
        strokeWidth="1"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

const BAR_HEIGHTS = [42, 64, 34, 74, 52, 84, 46];

function BarSkeleton() {
  return (
    <div className="flex h-full w-full items-end gap-2 px-1 pb-1">
      {BAR_HEIGHTS.map((h, i) => (
        <div
          key={i}
          className="flex-1 rounded-sm bg-current opacity-30"
          style={{ height: `${h}%` }}
        />
      ))}
    </div>
  );
}

function DonutSkeleton() {
  return (
    <div className="flex h-full w-full items-center justify-center">
      <svg
        aria-hidden="true"
        viewBox="0 0 100 100"
        className="h-full max-h-full w-auto"
        style={{ aspectRatio: "1 / 1" }}
      >
        <circle
          cx="50"
          cy="50"
          r="34"
          fill="none"
          stroke="currentColor"
          strokeOpacity="0.3"
          strokeWidth="16"
        />
      </svg>
    </div>
  );
}
