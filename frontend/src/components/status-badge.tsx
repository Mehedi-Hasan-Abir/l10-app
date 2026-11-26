import { CircleCheck, CircleCheckBig, CircleDashed, CircleX, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { RockStatus, Status } from "@/lib/api/types";

/**
 * One visual language for every status in the app: a colour, an icon and a text label together,
 * so meaning never depends on colour alone.
 */
export type AnyStatus = Status | RockStatus;

export interface StatusMeta {
  label: string;
  Icon: LucideIcon;
  /** Soft surface + readable text colour. */
  tone: string;
}

export const STATUS_META: Record<AnyStatus, StatusMeta> = {
  on_track: { label: "On track", Icon: CircleCheck, tone: "bg-ok-soft text-ok" },
  off_track: { label: "Off track", Icon: CircleX, tone: "bg-bad-soft text-bad" },
  pending: { label: "Pending", Icon: CircleDashed, tone: "bg-wait-soft text-wait" },
  complete: { label: "Complete", Icon: CircleCheckBig, tone: "bg-ok-soft text-ok" },
};

export function StatusBadge({ status, className }: { status: AnyStatus; className?: string }) {
  const { label, Icon, tone } = STATUS_META[status];
  return (
    <Badge variant="outline" className={cn("border-transparent font-medium", tone, className)}>
      <Icon data-icon="inline-start" aria-hidden />
      {label}
    </Badge>
  );
}
