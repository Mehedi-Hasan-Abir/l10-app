"use client";

import type { ReactNode } from "react";
import { AppHeader } from "@/components/app-header";
import { PageError } from "@/components/error-state";
import { IssueFocusProvider } from "@/components/issues/issue-focus";
import { Skeleton } from "@/components/ui/skeleton";
import { isApiError } from "@/lib/api/client";
import { useTeam } from "@/lib/api/queries";
import { TeamChannelProvider } from "@/lib/realtime/team-channel";
import { useRequireUser } from "@/lib/use-auth";

/**
 * Frame for everything under /teams/[teamId]: the header, the one realtime connection for the team
 * (so both pages stay live), and the friendly error state for a team the user can't open.
 */
export function TeamShell({ teamId, children }: { teamId: string; children: ReactNode }) {
  useRequireUser();
  const team = useTeam(teamId);
  const signedOut = isApiError(team.error) && team.error.status === 401;

  return (
    // Keyed by team so switching teams tears down one socket and opens the next.
    <TeamChannelProvider key={teamId} teamId={teamId}>
      <IssueFocusProvider>
        <a
          href="#main"
          className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
        >
          Skip to content
        </a>
        <div className="flex min-h-screen flex-col">
          <AppHeader teamId={teamId} />
          <main id="main" className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-5 lg:px-6">
            {signedOut ? (
              <Skeleton className="h-40 w-full" />
            ) : team.isError ? (
              <PageError error={team.error} onRetry={() => void team.refetch()} />
            ) : (
              children
            )}
          </main>
        </div>
      </IssueFocusProvider>
    </TeamChannelProvider>
  );
}
