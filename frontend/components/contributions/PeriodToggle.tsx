"use client";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

interface Props {
  value: "month" | "year";
  onChange: (v: "month" | "year") => void;
}

export function PeriodToggle({ value, onChange }: Props) {
  return (
    <ToggleGroup
      type="single"
      value={value}
      onValueChange={(v) => v && onChange(v as "month" | "year")}
      aria-label="Contribution period"
      className="min-h-11"
    >
      <ToggleGroupItem value="month">Month</ToggleGroupItem>
      <ToggleGroupItem value="year">Year</ToggleGroupItem>
    </ToggleGroup>
  );
}
