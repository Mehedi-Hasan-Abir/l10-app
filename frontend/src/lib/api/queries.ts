"use client";

import { infiniteQueryOptions, queryOptions, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api } from "./client";
import { keys } from "./keys";
import type {
  AuditPage,
  AuthUser,
  Issue,
  IssueStatusFilter,
  MeResponse,
  MeetingRecap,
  MeetingState,
  MeetingSummary,
  RocksResponse,
  Scorecard,
  Team,
  TeamRef,
  TodosResponse,
} from "./types";

/* Auth */

/** GET /api/auth/users: public list that feeds the sign-in user switcher. */
export function useAuthUsers() {
  return useQuery({
    queryKey: keys.users(),
    queryFn: ({ signal }) => api.get<AuthUser[]>("/api/auth/users", signal),
    staleTime: 5 * 60_000,
  });
}

/** GET /api/auth/me: current user and the teams they can open. A 401 means "not signed in". */
export function useMe() {
  return useQuery({
    queryKey: keys.me(),
    queryFn: ({ signal }) => api.get<MeResponse>("/api/auth/me", signal),
    staleTime: 60_000,
  });
}

/* Teams */

/** GET /api/teams */
export function useTeams() {
  return useQuery({
    queryKey: ["teams"] as const,
    queryFn: ({ signal }) => api.get<TeamRef[]>("/api/teams", signal),
  });
}

/** GET /api/teams/{id}: members, eligible owners, permissions, business clock, active meeting. */
export function useTeam(teamId: string) {
  return useQuery({
    queryKey: keys.teamDetail(teamId),
    queryFn: ({ signal }) => api.get<Team>(`/api/teams/${teamId}`, signal),
  });
}

/* Team data */

/** Query options are shared by the hooks and by `usePrefetchTeamData`, so both use identical keys and fetchers. */
export const scorecardOptions = (teamId: string) =>
  queryOptions({
    queryKey: keys.scorecard(teamId),
    queryFn: ({ signal }) => api.get<Scorecard>(`/api/teams/${teamId}/scorecard`, signal),
  });

export const issuesOptions = (teamId: string, status: IssueStatusFilter) =>
  queryOptions({
    queryKey: keys.issues(teamId, status),
    queryFn: ({ signal }) => api.get<Issue[]>(`/api/teams/${teamId}/issues?status=${status}`, signal),
  });

export const todosOptions = (teamId: string) =>
  queryOptions({
    queryKey: keys.todos(teamId),
    queryFn: ({ signal }) => api.get<TodosResponse>(`/api/teams/${teamId}/todos`, signal),
  });

export const rocksOptions = (teamId: string) =>
  queryOptions({
    queryKey: keys.rocks(teamId),
    queryFn: ({ signal }) => api.get<RocksResponse>(`/api/teams/${teamId}/rocks`, signal),
  });

/** GET /api/teams/{id}/scorecard */
export function useScorecard(teamId: string) {
  return useQuery(scorecardOptions(teamId));
}

/** GET /api/teams/{id}/issues?status=. Unsolved comes back in Issue order; never re-sort it. */
export function useIssues(teamId: string, status: IssueStatusFilter, enabled = true) {
  return useQuery({ ...issuesOptions(teamId, status), enabled });
}

/** GET /api/teams/{id}/todos */
export function useTodos(teamId: string) {
  return useQuery(todosOptions(teamId));
}

/** GET /api/teams/{id}/rocks */
export function useRocks(teamId: string) {
  return useQuery(rocksOptions(teamId));
}

/* Audit trail */

/** Events per request: the Activity card shows the last 20 and loads 20 more per click. */
export const AUDIT_PAGE_SIZE = 20;

/**
 * GET /api/teams/{id}/audit, newest first, keyset paginated. Each page's `next_before_id` is the cursor for the
 * next older page. When the query is invalidated TanStack Query refetches the loaded pages in order and derives
 * every cursor from the page it just fetched, so events written in between never leave a gap or a duplicate.
 */
export const auditOptions = (teamId: string) =>
  infiniteQueryOptions({
    queryKey: keys.audit(teamId),
    queryFn: ({ pageParam, signal }) =>
      api.get<AuditPage>(
        `/api/teams/${teamId}/audit?limit=${AUDIT_PAGE_SIZE}${pageParam === null ? "" : `&before_id=${pageParam}`}`,
        signal,
      ),
    initialPageParam: null as number | null,
    getNextPageParam: (lastPage) => lastPage.next_before_id,
  });

export function useAudit(teamId: string) {
  return useInfiniteQuery(auditOptions(teamId));
}

/* Meetings */

const firstMeeting = (list: MeetingState[]): MeetingState | null => list[0] ?? null;

/**
 * GET /api/teams/{id}/meetings?status=active. The cache holds the raw list so realtime messages can
 * write `[meeting]` / `[]` straight in; callers get the single active meeting or null.
 */
export function useActiveMeeting(teamId: string) {
  return useQuery({
    queryKey: keys.meetings(teamId, "active"),
    queryFn: ({ signal }) => api.get<MeetingState[]>(`/api/teams/${teamId}/meetings?status=active`, signal),
    select: firstMeeting,
  });
}

/** GET /api/teams/{id}/meetings?status=ended (newest first). */
export function useEndedMeetings(teamId: string, enabled = true) {
  return useQuery({
    queryKey: keys.meetings(teamId, "ended"),
    queryFn: ({ signal }) => api.get<MeetingState[]>(`/api/teams/${teamId}/meetings?status=ended`, signal),
    enabled,
  });
}

/**
 * GET /api/meetings/{id}. `dataUpdatedAt` is the client time of receipt, which the timer needs to
 * turn `server_now` into a clock offset.
 */
export function useMeeting(teamId: string, meetingId: string | null, enabled = true) {
  return useQuery({
    queryKey: keys.meeting(teamId, meetingId ?? "none"),
    queryFn: ({ signal }) => api.get<MeetingState>(`/api/meetings/${meetingId}`, signal),
    enabled: enabled && meetingId !== null,
  });
}

/** GET /api/meetings/{id}/recap: issues solved and to-dos created while the meeting ran. */
export function useMeetingRecap(teamId: string, meetingId: string | null) {
  return useQuery({
    queryKey: keys.recap(teamId, meetingId ?? "none"),
    queryFn: ({ signal }) => api.get<MeetingRecap>(`/api/meetings/${meetingId}/recap`, signal),
    enabled: meetingId !== null,
  });
}

/** GET /api/meetings/{id}/summary (ended meetings only). */
export function useMeetingSummary(teamId: string, meetingId: string | null) {
  return useQuery({
    queryKey: keys.summary(teamId, meetingId ?? "none"),
    queryFn: ({ signal }) => api.get<MeetingSummary>(`/api/meetings/${meetingId}/summary`, signal),
    enabled: meetingId !== null,
  });
}
