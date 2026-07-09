import { useId } from "react";

import { cn } from "@/lib/utils";

type ChartSkeletonVariant = "line" | "bar" | "donut";

/**
 * Chart-shaped loading placeholder. Faint gridlines/axis plus a ghosted
 * line / bar / donut silhouette, with a highlight band that sweeps along the
 * shapes (see globals.css `.skeleton-shimmer` for the shared box shimmer).
 * Size it with `className` to match the real chart's footprint (same height
 * classes) so the swap causes no layout shift. Reduced-motion hides the sweep
 * and leaves the static ghost.
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
      className={cn("text-muted-foreground", className)}
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
const LINE_POINTS = "0,46 14,32 28,38 42,20 56,28 70,14 84,24 100,10";
// Close the line down to the baseline (y=54) so the area beneath it can be filled.
const AREA_POINTS = `${LINE_POINTS} 100,54 0,54`;

const BAR_W = 9;
const BARS = [
  { x: 5, h: 20 },
  { x: 18.3, h: 31 },
  { x: 31.6, h: 16 },
  { x: 44.9, h: 36 },
  { x: 58.2, h: 25 },
  { x: 71.5, h: 40 },
  { x: 84.8, h: 22 },
];

/** Horizontal highlight gradient (transparent -> tint -> transparent) for the
 *  sweeping band. Not a component so it can sit inside <defs>. */
function shimmerGradient(id: string) {
  return (
    <linearGradient id={id} x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stopColor="currentColor" stopOpacity="0" />
      <stop offset="0.5" stopColor="currentColor" stopOpacity="0.55" />
      <stop offset="1" stopColor="currentColor" stopOpacity="0" />
    </linearGradient>
  );
}

/** The moving band, masked to the variant's ghost shapes, sweeping left->right.
 *  Hidden under reduced motion via the `chart-shimmer-band` class. */
function ShimmerBand({
  gradId,
  maskId,
  width,
  span,
  height,
}: {
  gradId: string;
  maskId: string;
  width: number;
  span: number;
  height: number;
}) {
  return (
    <rect
      className="chart-shimmer-band"
      x={-width}
      y="0"
      width={width}
      height={height}
      fill={`url(#${gradId})`}
      mask={`url(#${maskId})`}
    >
      <animate
        attributeName="x"
        from={-width}
        to={span}
        dur="1.5s"
        repeatCount="indefinite"
      />
    </rect>
  );
}

function LineSkeleton() {
  const areaId = useId();
  const gradId = useId();
  const maskId = useId();
  return (
    <svg
      aria-hidden="true"
      className="h-full w-full"
      viewBox="0 0 100 60"
      preserveAspectRatio="none"
    >
      <defs>
        {/* Whisper area: a faint tint under the line that fades to transparent. */}
        <linearGradient id={areaId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.18" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
        {shimmerGradient(gradId)}
        <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width="100" height="60">
          <polygon points={AREA_POINTS} fill="white" fillOpacity="0.5" />
          <polyline
            points={LINE_POINTS}
            fill="none"
            stroke="white"
            strokeWidth="3"
            vectorEffect="non-scaling-stroke"
          />
        </mask>
      </defs>
      {GRIDLINES.map((y) => (
        <line
          key={y}
          x1="0"
          x2="100"
          y1={y}
          y2={y}
          stroke="currentColor"
          strokeOpacity="0.13"
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
        />
      ))}
      <polygon points={AREA_POINTS} fill={`url(#${areaId})`} />
      <polyline
        points={LINE_POINTS}
        fill="none"
        stroke="currentColor"
        strokeOpacity="0.28"
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
        strokeOpacity="0.22"
        strokeWidth="1"
        vectorEffect="non-scaling-stroke"
      />
      <ShimmerBand gradId={gradId} maskId={maskId} width={45} span={100} height={60} />
    </svg>
  );
}

function BarSkeleton() {
  const gradId = useId();
  const maskId = useId();
  return (
    <svg
      aria-hidden="true"
      className="h-full w-full"
      viewBox="0 0 100 60"
      preserveAspectRatio="none"
    >
      <defs>
        {shimmerGradient(gradId)}
        <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width="100" height="60">
          {BARS.map((b, i) => (
            <rect key={i} x={b.x} y={54 - b.h} width={BAR_W} height={b.h} rx="1.5" fill="white" />
          ))}
        </mask>
      </defs>
      {BARS.map((b, i) => (
        <rect
          key={i}
          x={b.x}
          y={54 - b.h}
          width={BAR_W}
          height={b.h}
          rx="1.5"
          fill="currentColor"
          fillOpacity="0.22"
        />
      ))}
      <ShimmerBand gradId={gradId} maskId={maskId} width={45} span={100} height={60} />
    </svg>
  );
}

function DonutSkeleton() {
  const gradId = useId();
  const maskId = useId();
  return (
    <div className="flex h-full w-full items-center justify-center">
      <svg
        aria-hidden="true"
        viewBox="0 0 100 100"
        className="h-full max-h-full w-auto"
        style={{ aspectRatio: "1 / 1" }}
      >
        <defs>
          {shimmerGradient(gradId)}
          <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width="100" height="100">
            <circle cx="50" cy="50" r="34" fill="none" stroke="white" strokeWidth="16" />
          </mask>
        </defs>
        <circle
          cx="50"
          cy="50"
          r="34"
          fill="none"
          stroke="currentColor"
          strokeOpacity="0.28"
          strokeWidth="16"
        />
        <ShimmerBand gradId={gradId} maskId={maskId} width={45} span={100} height={100} />
      </svg>
    </div>
  );
}
