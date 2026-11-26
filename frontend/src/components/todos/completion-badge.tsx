import { CircleCheck, CircleSlash, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Completion } from "@/lib/api/types";
import { formatWeekLabel } from "@/lib/time";
import { cn } from "@/lib/utils";

/** The completion target is fixed by the product rules; shown for context, never computed here. */
const TARGET_PERCENT = 90;

/** "Last week: 90% ✓ target 90%", or "No to-dos due" when nothing was due. */
export function CompletionBadge({
  completion,
  lastClosedWeek,
}: {
  completion: Completion | null;
  lastClosedWeek: string;
}) {
  if (!completion) {
    return (
      <Badge variant="outline" className="h-auto py-0.5 font-normal text-muted-foreground">
        <CircleSlash data-icon="inline-start" aria-hidden />
        Last week: no to-dos due
        <span className="sr-only">(week of {formatWeekLabel(lastClosedWeek)})</span>
      </Badge>
    );
  }

  const Icon = completion.meets_target ? CircleCheck : TriangleAlert;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="outline"
          tabIndex={0}
          className={cn(
            "h-auto cursor-default border-transparent py-0.5 font-medium",
            completion.meets_target ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn",
          )}
        >
          <Icon data-icon="inline-start" aria-hidden />
          Last week: {completion.rate_percent}% {completion.meets_target ? "✓" : "✗"}
          <span className="font-normal opacity-80">target {TARGET_PERCENT}%</span>
          <span className="sr-only">{completion.meets_target ? "meets target" : "below target"}</span>
        </Badge>
      </TooltipTrigger>
      <TooltipContent>
        {completion.done} of {completion.due} To-Dos due in the week of {formatWeekLabel(completion.week_start)} were
        done by the end of that week.
      </TooltipContent>
    </Tooltip>
  );
}
