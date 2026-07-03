"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export type Timeframe = "1m" | "3m" | "1y" | "all";

const LABELS: Record<Timeframe, string> = {
  "1m": "Last 1 month",
  "3m": "Last 3 months",
  "1y": "Last 1 year",
  all: "All time",
};

interface Props {
  value: Timeframe;
  onChange: (v: Timeframe) => void;
}

export function FxTimeframeSelect({ value, onChange }: Props) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as Timeframe)}>
      <SelectTrigger className="w-[180px]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {(Object.keys(LABELS) as Timeframe[]).map((tf) => (
          <SelectItem key={tf} value={tf}>
            {LABELS[tf]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
