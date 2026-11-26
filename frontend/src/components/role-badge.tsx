import { Badge } from "@/components/ui/badge";
import type { Role } from "@/lib/api/types";
import { ROLE_LABEL } from "@/lib/permissions";
import { cn } from "@/lib/utils";

const ROLE_TONE: Record<Role, string> = {
  admin: "bg-primary text-primary-foreground",
  team_member: "bg-accent text-accent-foreground",
  observer: "border-border bg-transparent text-muted-foreground",
};

export function RoleBadge({ role, className }: { role: Role; className?: string }) {
  return (
    <Badge variant="outline" className={cn("border-transparent font-medium", ROLE_TONE[role], className)}>
      {ROLE_LABEL[role]}
    </Badge>
  );
}
