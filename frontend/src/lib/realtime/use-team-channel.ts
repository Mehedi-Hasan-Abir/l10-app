"use client";

import { useEffect, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { keys } from "@/lib/api/keys";
import { writeMeetingSnapshot, writeMeetingState } from "@/lib/api/meeting-cache";
import type { RealtimeMessage, RealtimeResource } from "@/lib/api/types";

/**
 * Team WebSocket (PLAN section 7). The server only notifies; Postgres owns the data. So this hook
 * either writes a received MeetingState straight into the query cache, or invalidates query keys and
 * lets TanStack Query refetch. After every (re)connect it invalidates all of the team's queries, which
 * is how anything missed while disconnected is recovered.
 */

export type ChannelStatus = "connecting" | "live" | "reconnecting" | "denied";

const BACKOFF_START_MS = 500;
const BACKOFF_CAP_MS = 5_000;
const CLOSE_UNAUTHENTICATED = 4401;
const CLOSE_FORBIDDEN = 4403;

function channelUrl(teamId: string): string {
  const configured = process.env.NEXT_PUBLIC_WS_URL;
  const origin = configured
    ? configured.replace(/\/$/, "")
    : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;
  return `${origin}/ws/teams/${teamId}`;
}

/** Map a realtime resource name to the query keys it dirties. */
function invalidateResource(qc: QueryClient, teamId: string, resource: RealtimeResource): void {
  switch (resource) {
    case "scorecard":
      void qc.invalidateQueries({ queryKey: keys.scorecard(teamId) });
      return;
    case "issues":
      void qc.invalidateQueries({ queryKey: keys.issuesAll(teamId) });
      void qc.invalidateQueries({ queryKey: ["team", teamId, "meeting"], predicate: isRecap });
      return;
    case "todos":
      void qc.invalidateQueries({ queryKey: keys.todos(teamId) });
      void qc.invalidateQueries({ queryKey: ["team", teamId, "meeting"], predicate: isRecap });
      return;
    case "rocks":
      void qc.invalidateQueries({ queryKey: keys.rocks(teamId) });
      return;
    case "meetings":
      void qc.invalidateQueries({ queryKey: keys.meetingsAll(teamId) });
      return;
  }
}

function isRecap(query: { queryKey: readonly unknown[] }): boolean {
  return query.queryKey[4] === "recap";
}

function parseMessage(data: unknown): RealtimeMessage | null {
  if (typeof data !== "string") return null;
  try {
    const parsed: unknown = JSON.parse(data);
    if (parsed && typeof parsed === "object" && "type" in parsed) return parsed as RealtimeMessage;
  } catch {
    // ignore malformed frames
  }
  return null;
}

function handleMessage(qc: QueryClient, teamId: string, message: RealtimeMessage): void {
  switch (message.type) {
    case "snapshot":
      writeMeetingSnapshot(qc, teamId, message.meeting);
      return;
    case "meeting":
      writeMeetingState(qc, message.meeting);
      // Meeting steps (start, advance, rate, end) write audit events; a presence-only message just refetches.
      void qc.invalidateQueries({ queryKey: keys.audit(teamId) });
      return;
    case "invalidate":
      for (const resource of message.resources) invalidateResource(qc, teamId, resource);
      // Every data mutation writes an audit event in the same transaction, whatever resource it dirtied.
      void qc.invalidateQueries({ queryKey: keys.audit(teamId) });
      return;
    case "ping":
      return;
  }
}

/** Connect to `/ws/teams/{teamId}` for the lifetime of the calling component. Mount once per team. */
export function useTeamChannel(teamId: string): ChannelStatus {
  const qc = useQueryClient();
  const [status, setStatus] = useState<ChannelStatus>("connecting");

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let disposed = false;

    const connect = () => {
      clearTimeout(retryTimer);
      const ws = new WebSocket(channelUrl(teamId));
      socket = ws;

      ws.onopen = () => {
        if (disposed) {
          ws.close();
          return;
        }
        attempt = 0;
        setStatus("live");
        // Anything published while we were away is gone; refetch the team's data to catch up.
        void qc.invalidateQueries({ queryKey: keys.team(teamId) });
      };

      ws.onmessage = (event) => {
        const message = parseMessage(event.data);
        if (message) handleMessage(qc, teamId, message);
      };

      ws.onclose = (event) => {
        if (socket === ws) socket = null;
        if (disposed) return;
        if (event.code === CLOSE_UNAUTHENTICATED || event.code === CLOSE_FORBIDDEN) {
          setStatus("denied");
          if (event.code === CLOSE_UNAUTHENTICATED) void qc.invalidateQueries({ queryKey: keys.me() });
          return;
        }
        setStatus("reconnecting");
        const delay = Math.min(BACKOFF_START_MS * 2 ** attempt, BACKOFF_CAP_MS);
        attempt += 1;
        retryTimer = setTimeout(connect, delay);
      };

      // `onerror` is always followed by `onclose`, which owns the retry.
    };

    // Coming back online or to the foreground: don't wait out the backoff timer.
    const retryNow = () => {
      if (disposed || socket !== null) return;
      attempt = 0;
      connect();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") retryNow();
    };

    connect();
    window.addEventListener("online", retryNow);
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      disposed = true;
      clearTimeout(retryTimer);
      window.removeEventListener("online", retryNow);
      document.removeEventListener("visibilitychange", onVisible);
      // Closing a socket that is still CONNECTING logs a browser error; let it open, then close.
      if (socket && socket.readyState === WebSocket.CONNECTING) {
        const pending = socket;
        pending.onopen = () => pending.close();
      } else {
        socket?.close();
      }
    };
  }, [teamId, qc]);

  return status;
}
