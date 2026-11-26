"use client";

import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

/** Issue order is 5 first, down to 1; no priority comes last. */
export const PRIORITY_LEVELS = [5, 4, 3, 2, 1] as const;

const LEVEL_NOTE: Record<number, string> = { 5: "Highest", 1: "Lowest" };

/** Darker ink = more urgent. The number is always printed, so the shade is a bonus, not the signal. */
const LEVEL_TONE: Record<number, string> = {
  5: "bg-primary text-primary-foreground",
  4: "bg-primary/80 text-primary-foreground",
  3: "bg-accent text-accent-foreground",
  2: "bg-secondary text-secondary-foreground",
  1: "bg-muted text-muted-foreground",
};

export function PriorityChip({ priority, className }: { priority: number | null; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-6 min-w-9 items-center justify-center rounded-md px-1.5 font-mono text-xs font-medium tabular-nums",
        priority === null ? "border border-dashed border-border text-muted-foreground" : LEVEL_TONE[priority],
        className,
      )}
    >
      {priority === null ? "P–" : `P${priority}`}
    </span>
  );
}

function priorityWords(priority: number | null): string {
  return priority === null ? "no priority" : `priority ${priority}`;
}

export interface PriorityPickerProps {
  issueTitle: string;
  priority: number | null;
  /** Observers and solved issues just see the chip. */
  editable: boolean;
  onChange: (priority: number | null) => void;
}

export function PriorityPicker({ issueTitle, priority, editable, onChange }: PriorityPickerProps) {
  if (!editable) {
    return (
      <span title={`Priority: ${priority ?? "none"}`}>
        <PriorityChip priority={priority} />
        <span className="sr-only">Priority: {priority ?? "none"}</span>
      </span>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="xs"
          className="h-6 gap-0.5 px-0.5"
          aria-label={`Priority for "${issueTitle}": ${priorityWords(priority)}. Change`}
        >
          <PriorityChip priority={priority} />
          <ChevronDown className="text-muted-foreground" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-44">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Priority</DropdownMenuLabel>
          <DropdownMenuRadioGroup
            value={priority === null ? "none" : String(priority)}
            onValueChange={(value) => onChange(value === "none" ? null : Number(value))}
          >
            {PRIORITY_LEVELS.map((level) => (
              <DropdownMenuRadioItem key={level} value={String(level)}>
                {level}
                {LEVEL_NOTE[level] ? (
                  <span className="ml-1 text-xs text-muted-foreground">· {LEVEL_NOTE[level]}</span>
                ) : null}
              </DropdownMenuRadioItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuRadioItem value="none">No priority</DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
