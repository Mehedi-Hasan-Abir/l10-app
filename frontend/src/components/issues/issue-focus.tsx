"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

/**
 * Lets any component (a score cell's issue chip, a toast action) point at an issue in the Issues panel.
 * The panel scrolls to it and flashes it once. Purely UI state, so it lives in context, not in the cache.
 */
interface IssueFocusValue {
  focusedIssueId: string | null;
  focusIssue: (issueId: string) => void;
  clearFocus: () => void;
}

const IssueFocusContext = createContext<IssueFocusValue | null>(null);

const FLASH_MS = 2600;

export function IssueFocusProvider({ children }: { children: ReactNode }) {
  const [focusedIssueId, setFocusedIssueId] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const clearFocus = useCallback(() => {
    clearTimeout(timer.current);
    setFocusedIssueId(null);
  }, []);

  const focusIssue = useCallback((issueId: string) => {
    clearTimeout(timer.current);
    setFocusedIssueId(issueId);
    timer.current = setTimeout(() => setFocusedIssueId(null), FLASH_MS);
  }, []);

  useEffect(() => () => clearTimeout(timer.current), []);

  const value = useMemo(() => ({ focusedIssueId, focusIssue, clearFocus }), [focusedIssueId, focusIssue, clearFocus]);
  return <IssueFocusContext.Provider value={value}>{children}</IssueFocusContext.Provider>;
}

export function useIssueFocus(): IssueFocusValue {
  const value = useContext(IssueFocusContext);
  if (!value) throw new Error("useIssueFocus must be used inside <IssueFocusProvider>");
  return value;
}
