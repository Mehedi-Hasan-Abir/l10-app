"use client";

import { useEffect, useState } from "react";

/**
 * The meeting timer is owned by the server. The browser clock may be wrong, so we never compare
 * `Date.now()` with a server timestamp directly. Instead, when a MeetingState is received we note
 * how far the server's `server_now` is from our own clock (the offset) and apply it to every tick.
 */

/**
 * offset = server_now - (client time at receipt). `receivedAt` is the query's `dataUpdatedAt`,
 * which is the client time the state arrived, so the offset stays right even after a remount
 * re-reads older cached data.
 */
export function useServerOffset(serverNow: string, receivedAt: number): number {
  return Date.parse(serverNow) - receivedAt;
}

/** Client time in ms, re-read every `intervalMs`. */
function useTicker(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0); // catch up immediately after a (re)mount
    const id = setInterval(tick, intervalMs);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [intervalMs]);

  return now;
}

/** Current server time in ms, ticking every `intervalMs` (default 250 ms). */
export function useServerNow(serverNow: string, receivedAt: number, intervalMs = 250): number {
  const offset = useServerOffset(serverNow, receivedAt);
  const clientNow = useTicker(intervalMs);
  return clientNow + offset;
}
