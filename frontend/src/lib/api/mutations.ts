"use client";

import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, detailField, errorMessage, isApiError } from "./client";
import { keys } from "./keys";
import { writeMeetingState } from "./meeting-cache";
import type {
  AdvanceBody,
  CreateIssueBody,
  CreateTodoBody,
  Issue,
  LoginResponse,
  MakeIssueResponse,
  MeetingState,
  PatchIssueBody,
  ScoreCell,
  Scorecard,
  SectionKey,
  SolveIssueBody,
  Todo,
  TodosResponse,
} from "./types";

/* ------------------------------------------------------------------ helpers */

function patchCell(
  scorecard: Scorecard | undefined,
  measurableId: string,
  weekStart: string,
  update: (cell: ScoreCell) => ScoreCell,
): Scorecard | undefined {
  if (!scorecard) return scorecard;
  return {
    ...scorecard,
    measurables: scorecard.measurables.map((m) =>
      m.id !== measurableId
        ? m
        : {
            ...m,
            cells: m.cells.map((c) => (c.week_start === weekStart ? update(c) : c)),
          },
    ),
  };
}

/** Recap queries live under the meeting key; refresh them when issues or to-dos change. */
function invalidateRecaps(qc: QueryClient, teamId: string): void {
  void qc.invalidateQueries({
    queryKey: ["team", teamId, "meeting"],
    predicate: (query) => query.queryKey[4] === "recap",
  });
}

/* --------------------------------------------------------------------- auth */

/** POST /api/auth/login */
export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => api.post<LoginResponse>("/api/auth/login", { user_id: userId }),
    onSuccess: () => {
      // Another user's cached team data must never leak into this session.
      qc.removeQueries({ queryKey: ["team"] });
      qc.removeQueries({ queryKey: keys.me() });
    },
  });
}

/** POST /api/auth/logout */
export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>("/api/auth/logout"),
    onSuccess: () => {
      qc.removeQueries({ queryKey: ["team"] });
      qc.removeQueries({ queryKey: keys.me() });
    },
  });
}

/* ---------------------------------------------------------------- scorecard */

export interface PutScoreVariables {
  measurableId: string;
  weekStart: string;
  value: number | null;
  /** The cell's current `version`, or null when the cell has no score yet. */
  expectedVersion: number | null;
}

interface PutScoreContext {
  previous: Scorecard | undefined;
}

/**
 * PUT /api/teams/{id}/measurables/{mid}/scores/{week}. Optimistic: the new value shows immediately,
 * the server's cell (with the computed status) replaces it on success, and a stale `expected_version`
 * (409 `version_conflict`) swaps in the newer value and says so.
 */
export function usePutScore(teamId: string) {
  const qc = useQueryClient();
  const mutationKey = ["put-score", teamId] as const;

  return useMutation<ScoreCell, Error, PutScoreVariables, PutScoreContext>({
    mutationKey,
    meta: { skipErrorToast: true },
    mutationFn: (v) =>
      api.put<ScoreCell>(`/api/teams/${teamId}/measurables/${v.measurableId}/scores/${v.weekStart}`, {
        value: v.value,
        ...(v.expectedVersion !== null ? { expected_version: v.expectedVersion } : {}),
      }),
    onMutate: async (v) => {
      await qc.cancelQueries({ queryKey: keys.scorecard(teamId) });
      const previous = qc.getQueryData<Scorecard>(keys.scorecard(teamId));
      qc.setQueryData<Scorecard>(keys.scorecard(teamId), (cur) =>
        patchCell(cur, v.measurableId, v.weekStart, (c) => ({
          ...c,
          value: v.value,
        })),
      );
      return { previous };
    },
    onSuccess: (cell, v) => {
      qc.setQueryData<Scorecard>(keys.scorecard(teamId), (cur) =>
        patchCell(cur, v.measurableId, v.weekStart, () => cell),
      );
    },
    onError: (error, v, context) => {
      const current =
        isApiError(error) && error.code === "version_conflict" ? detailField<ScoreCell>(error, "current") : undefined;
      if (current) {
        qc.setQueryData<Scorecard>(keys.scorecard(teamId), (cur) =>
          patchCell(cur, v.measurableId, v.weekStart, () => current),
        );
        toast.warning("Someone else just changed this score", {
          description: `Showing their value (${current.value ?? "blank"}). Enter yours again if it still applies.`,
        });
        return;
      }
      if (context) qc.setQueryData(keys.scorecard(teamId), context.previous);
      toast.error(errorMessage(error));
    },
    onSettled: () => {
      // Only reconcile once the last in-flight edit lands, so a slow response can't clobber a newer optimistic one.
      if (qc.isMutating({ mutationKey }) <= 1) void qc.invalidateQueries({ queryKey: keys.scorecard(teamId) });
    },
  });
}

export interface MakeIssueVariables {
  measurableId: string;
  weekStart: string;
}

/** POST /api/teams/{id}/measurables/{mid}/scores/{week}/issue (201 created, 200 already exists). */
export function useMakeIssue(teamId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: MakeIssueVariables) =>
      api.post<MakeIssueResponse>(`/api/teams/${teamId}/measurables/${v.measurableId}/scores/${v.weekStart}/issue`),
    onSuccess: ({ issue }, v) => {
      qc.setQueryData<Scorecard>(keys.scorecard(teamId), (cur) =>
        patchCell(cur, v.measurableId, v.weekStart, (c) => ({
          ...c,
          open_issue_id: issue.id,
        })),
      );
      void qc.invalidateQueries({ queryKey: keys.issuesAll(teamId) });
    },
    onError: () => {
      // e.g. 409 not_off_track: the cell changed under us.
      void qc.invalidateQueries({ queryKey: keys.scorecard(teamId) });
    },
  });
}

export interface MakeIssueFromVariables {
  source: "rock" | "todo";
  id: string;
}

/**
 * Bonus: POST /api/teams/{id}/rocks/{rockId}/issue (off-track Rock) or …/todos/{todoId}/issue (overdue
 * To-Do). Same no-duplicates rule as the scorecard: 201 new, 200 returns the open one.
 */
export function useMakeIssueFrom(teamId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ source, id }: MakeIssueFromVariables) =>
      api.post<MakeIssueResponse>(`/api/teams/${teamId}/${source}s/${id}/issue`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.issuesAll(teamId) });
    },
    onError: (_error, { source }) => {
      // e.g. 409: the rock is no longer off track / the to-do is no longer overdue.
      void qc.invalidateQueries({
        queryKey: source === "rock" ? keys.rocks(teamId) : keys.todos(teamId),
      });
    },
  });
}

/* ------------------------------------------------------------------- issues */

/** POST /api/teams/{id}/issues */
export function useCreateIssue(teamId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateIssueBody) => api.post<Issue>(`/api/teams/${teamId}/issues`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.issuesAll(teamId) });
    },
  });
}

export interface SetPriorityVariables {
  issue: Issue;
  priority: number | null;
}

interface SetPriorityContext {
  previous: Issue[] | undefined;
}

/**
 * PATCH /api/teams/{id}/issues/{iid}. Optimistic and deliberately NOT re-sorted locally: ordering is
 * server-owned, so the row moves when the refetch lands.
 */
export function useSetIssuePriority(teamId: string) {
  const qc = useQueryClient();
  const listKey = keys.issues(teamId, "unsolved");

  return useMutation<Issue, Error, SetPriorityVariables, SetPriorityContext>({
    meta: { skipErrorToast: true },
    mutationFn: ({ issue, priority }) => {
      const body: PatchIssueBody = {
        priority,
        expected_version: issue.version,
      };
      return api.patch<Issue>(`/api/teams/${teamId}/issues/${issue.id}`, body);
    },
    onMutate: async ({ issue, priority }) => {
      await qc.cancelQueries({ queryKey: listKey });
      const previous = qc.getQueryData<Issue[]>(listKey);
      qc.setQueryData<Issue[]>(listKey, (cur) => cur?.map((i) => (i.id === issue.id ? { ...i, priority } : i)));
      return { previous };
    },
    onSuccess: (updated) => {
      qc.setQueryData<Issue[]>(listKey, (cur) => cur?.map((i) => (i.id === updated.id ? updated : i)));
    },
    onError: (error, _v, context) => {
      if (context) qc.setQueryData(listKey, context.previous);
      if (isApiError(error) && error.code === "version_conflict") {
        toast.warning("This issue was just changed by someone else", {
          description: "Showing the latest version. Set the priority again if it still applies.",
        });
      } else {
        toast.error(errorMessage(error));
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: keys.issuesAll(teamId) });
    },
  });
}

export interface SolveIssueVariables {
  issueId: string;
  body: SolveIssueBody;
}

/** POST /api/teams/{id}/issues/{iid}/solve */
export function useSolveIssue(teamId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ issueId, body }: SolveIssueVariables) =>
      api.post<Issue>(`/api/teams/${teamId}/issues/${issueId}/solve`, body),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: keys.issuesAll(teamId) });
      // A solved issue no longer links its score cell.
      void qc.invalidateQueries({ queryKey: keys.scorecard(teamId) });
      invalidateRecaps(qc, teamId);
    },
  });
}

export interface AddTodoVariables {
  issueId: string;
  body: CreateTodoBody;
}

/** POST /api/teams/{id}/issues/{iid}/todos (due in 7 days, linked to the issue). */
export function useAddTodoToIssue(teamId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ issueId, body }: AddTodoVariables) =>
      api.post<Todo>(`/api/teams/${teamId}/issues/${issueId}/todos`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.todos(teamId) });
      void qc.invalidateQueries({ queryKey: keys.issuesAll(teamId) });
      invalidateRecaps(qc, teamId);
    },
  });
}

/* -------------------------------------------------------------------- todos */

export interface SetTodoDoneVariables {
  todoId: string;
  done: boolean;
}

interface SetTodoDoneContext {
  previous: TodosResponse | undefined;
}

/** PATCH /api/teams/{id}/todos/{tid}. Optimistic toggle; the refetch brings the real done time and rate. */
export function useSetTodoDone(teamId: string) {
  const qc = useQueryClient();
  const key = keys.todos(teamId);

  return useMutation<Todo, Error, SetTodoDoneVariables, SetTodoDoneContext>({
    mutationFn: ({ todoId, done }) => api.patch<Todo>(`/api/teams/${teamId}/todos/${todoId}`, { done }),
    onMutate: async ({ todoId, done }) => {
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<TodosResponse>(key);
      qc.setQueryData<TodosResponse>(key, (cur) =>
        cur
          ? {
              ...cur,
              todos: cur.todos.map((t) =>
                t.id === todoId
                  ? {
                      ...t,
                      done,
                      done_at: done ? t.done_at : null,
                      overdue: done ? false : t.overdue,
                    }
                  : t,
              ),
            }
          : cur,
      );
      return { previous };
    },
    onError: (_error, _v, context) => {
      if (context) qc.setQueryData(key, context.previous);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: key });
      invalidateRecaps(qc, teamId);
    },
  });
}

/* ----------------------------------------------------------------- meetings */

/** POST /api/teams/{id}/meetings: the caller becomes facilitator. */
export function useStartMeeting(teamId: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { skipErrorToast: true },
    mutationFn: () => api.post<MeetingState>(`/api/teams/${teamId}/meetings`),
    onSuccess: (meeting) => writeMeetingState(qc, meeting),
    onError: (error) => {
      if (isApiError(error) && error.code === "meeting_already_active") {
        // Lost the race to another starter: show the meeting that exists.
        void qc.invalidateQueries({
          queryKey: keys.meetings(teamId, "active"),
        });
        toast.info("A meeting is already running", {
          description: "Joining it instead.",
        });
      } else {
        toast.error(errorMessage(error));
      }
    },
  });
}

/** POST /api/meetings/{id}/join (idempotent). */
export function useJoinMeeting(teamId: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { skipErrorToast: true },
    mutationFn: (meetingId: string) => api.post<MeetingState>(`/api/meetings/${meetingId}/join`),
    onSuccess: (meeting) => writeMeetingState(qc, meeting),
    onError: () => {
      // 409 meeting_ended or a lost permission: re-read the truth instead of nagging.
      void qc.invalidateQueries({ queryKey: keys.meetingsAll(teamId) });
    },
  });
}

/** POST /api/meetings/{id}/advance (facilitator only). */
export interface AdvanceMeetingVariables {
  teamId: string;
  meetingId: string;
  /** The section the facilitator is looking at; a stale value gets 409 `stale_section`, never a skip. */
  fromSection: SectionKey;
}

/** POST /api/meetings/{id}/advance (facilitator only). */
export function useAdvanceMeeting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ meetingId, fromSection }: AdvanceMeetingVariables) =>
      api.post<MeetingState>(`/api/meetings/${meetingId}/advance`, {
        from_section: fromSection,
      } satisfies AdvanceBody),
    onSuccess: (meeting) => writeMeetingState(qc, meeting),
    onError: (error, { teamId, meetingId }) => {
      // Another tab or a double-click already moved the meeting on: show where it is now.
      if (isApiError(error) && error.code === "stale_section") {
        void qc.invalidateQueries({
          queryKey: keys.meeting(teamId, meetingId),
        });
        void qc.invalidateQueries({ queryKey: keys.meetingsAll(teamId) });
      }
    },
  });
}

/** POST /api/meetings/{id}/end (facilitator only). */
export function useEndMeeting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (meetingId: string) => api.post<MeetingState>(`/api/meetings/${meetingId}/end`),
    onSuccess: (meeting) => writeMeetingState(qc, meeting),
  });
}

export interface RateMeetingVariables {
  meetingId: string;
  rating: number;
}

/** PUT /api/meetings/{id}/rating (Conclude only; changeable until the meeting ends). */
export function useRateMeeting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ meetingId, rating }: RateMeetingVariables) =>
      api.put<MeetingState>(`/api/meetings/${meetingId}/rating`, { rating }),
    onSuccess: (meeting) => writeMeetingState(qc, meeting),
  });
}
