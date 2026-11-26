"use client";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

const RATINGS = Array.from({ length: 10 }, (_, i) => i + 1);

export interface RatingPickerProps {
  /** The caller's current rating, if any. It can be changed until the meeting ends. */
  value: number | null;
  onChange: (rating: number) => void;
  disabled?: boolean;
}

/** 1 to 10, one choice, arrow-key navigable. Picking the selected number again keeps it selected. */
export function RatingPicker({ value, onChange, disabled }: RatingPickerProps) {
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size="lg"
      spacing={1}
      value={value === null ? "" : String(value)}
      onValueChange={(next) => {
        // Radix reports "" when the selected item is clicked again; a rating can't be un-set.
        if (next) onChange(Number(next));
      }}
      disabled={disabled}
      aria-label="Rate this meeting from 1 to 10"
      className="flex-wrap"
    >
      {RATINGS.map((n) => (
        <ToggleGroupItem
          key={n}
          value={String(n)}
          aria-label={`${n} out of 10`}
          className="w-10 font-mono text-base tabular-nums data-[state=on]:border-primary data-[state=on]:bg-primary data-[state=on]:text-primary-foreground"
        >
          {n}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
