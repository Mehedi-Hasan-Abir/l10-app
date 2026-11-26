"use client";

import { cn } from "@/lib/utils";
import { useChannelStatus } from "@/lib/realtime/team-channel";
import type { ChannelStatus } from "@/lib/realtime/use-team-channel";

const COPY: Record<ChannelStatus, { label: string; dot: string; text: string }> = {
  live: { label: "Live", dot: "bg-ok", text: "text-ok" },
  connecting: { label: "Connecting…", dot: "bg-wait animate-pulse", text: "text-muted-foreground" },
  reconnecting: { label: "Reconnecting…", dot: "bg-warn animate-pulse", text: "text-warn" },
  denied: { label: "Offline", dot: "bg-bad", text: "text-bad" },
};

/** Small realtime health pill: "Live" when the team socket is open, "Reconnecting…" while it recovers. */
export function ConnectionIndicator({ className }: { className?: string }) {
  const status = useChannelStatus();
  const { label, dot, text } = COPY[status];
  return (
    <span
      role="status"
      aria-live="polite"
      title={
        status === "live"
          ? "Realtime updates are on"
          : status === "denied"
            ? "Live updates are unavailable for this team"
            : "Trying to restore live updates. Changes are saved either way."
      }
      className={cn("inline-flex items-center gap-1.5 text-xs font-medium", text, className)}
    >
      <span aria-hidden className={cn("size-2 rounded-full", dot)} />
      {label}
    </span>
  );
}
