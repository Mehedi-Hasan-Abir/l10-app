"use client";

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { auditOptions, issuesOptions, rocksOptions, scorecardOptions, todosOptions } from "./queries";

export type PrefetchResource = "scorecard" | "issues" | "todos" | "rocks" | "audit";

/**
 * Start a page's data requests in parallel with the team request instead of after it.
 * A page waits for `GET /teams/{id}` (permissions) before drawing its panels; without this, each panel
 * would only begin fetching once that returned. Prefetching respects `staleTime` and never throws.
 * "audit" prefetches the first page of the Activity card (the infinite query's initial page only).
 */
export function usePrefetchTeamData(teamId: string, resources: readonly PrefetchResource[]): void {
  const qc = useQueryClient();
  const wanted = resources.join(",");

  useEffect(() => {
    const set = new Set(wanted.split(","));
    if (set.has("scorecard")) void qc.prefetchQuery(scorecardOptions(teamId));
    if (set.has("issues")) void qc.prefetchQuery(issuesOptions(teamId, "unsolved"));
    if (set.has("todos")) void qc.prefetchQuery(todosOptions(teamId));
    if (set.has("rocks")) void qc.prefetchQuery(rocksOptions(teamId));
    if (set.has("audit")) void qc.prefetchInfiniteQuery(auditOptions(teamId));
  }, [qc, teamId, wanted]);
}
