import { Gauge, ListChecks, Mountain } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { IssueSource } from "@/lib/api/types";
import { formatWeekLabel } from "@/lib/time";

/** Where an issue came from: "New deals closed · week of Nov 2". Manually created issues have no source. */
export function SourceChip({ source }: { source: IssueSource | null }) {
  if (!source) return null;

  switch (source.type) {
    case "measurable":
      return (
        <Badge variant="outline" className="h-auto max-w-full py-0.5 font-normal">
          <Gauge data-icon="inline-start" aria-hidden />
          <span className="truncate">
            {source.measurable_name} · week of {formatWeekLabel(source.week_start)}
          </span>
        </Badge>
      );
    case "rock":
      return (
        <Badge variant="outline" className="h-auto max-w-full py-0.5 font-normal">
          <Mountain data-icon="inline-start" aria-hidden />
          <span className="truncate">Rock · {source.rock_title}</span>
        </Badge>
      );
    case "todo":
      return (
        <Badge variant="outline" className="h-auto max-w-full py-0.5 font-normal">
          <ListChecks data-icon="inline-start" aria-hidden />
          <span className="truncate">To-Do · {source.todo_title}</span>
        </Badge>
      );
  }
}
